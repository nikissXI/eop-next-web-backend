from io import BytesIO
from database import *
from models import *
from services import *
from utils import *
from utils.config import *
from time import strftime, localtime
from asyncio import create_task, gather


class BotNotFound(Exception):
    def __init__(self):
        pass


class NoChat(Exception):
    def __init__(self):
        pass


class ModelNotFound(Exception):
    def __init__(self, model: str):
        self.model = model


class BotDisable(Exception):
    def __init__(self):
        pass


class UserOutdate(Exception):
    def __init__(self, date: int):
        date_string = strftime("%Y-%m-%d %H:%M:%S", localtime(date / 1000))
        self.date = date_string


class LevelError(Exception):
    def __init__(self):
        pass


def handle_exception(err_msg: str) -> JSONResponse:
    """处理poe请求错误"""
    logger.error(err_msg)
    return JSONResponse({"code": 3001, "msg": err_msg}, 500)


async def check_bot_hoster(uid: int, eop_id: str):
    if not await Bot.check_bot_user(eop_id, uid):
        raise BotNotFound()


async def check_chat_exist(id: int):
    if not id:
        raise NoChat()


async def check_user_level(uid: int, model: str):
    level = await User.get_level(uid)
    info = poe.client.offical_models[model]
    if info.limited and level == 1:
        raise LevelError()


async def check_user_outdate(uid: int):
    if await User.is_outdate(uid):
        expire_date = await User.get_expire_date(uid)
        raise UserOutdate(expire_date)


router = APIRouter()


@router.get(
    "/models",
    summary="获取可用模型",
    responses={
        200: {
            "description": "diy指是否可以设置prompt，limited指是否有使用次数限制",
            "content": {
                "application/json": {
                    "example": {
                        "available_models": [
                            {
                                "model": "ChatGPT",
                                "description": "由gpt-3.5-turbo驱动。",
                                "diy": True,
                                "limited": False,
                            },
                            {
                                "model": "GPT-4",
                                "description": "OpenAI最强大的模型。在定量问题（数学和物理）、创造性写作和许多其他具有挑战性的任务方面比ChatGPT更强大。",
                                "diy": True,
                                "limited": True,
                            },
                            {
                                "model": "Google-PaLM",
                                "description": "由Google的PaLM 2 chat-bison-001模型驱动。",
                                "diy": False,
                                "limited": False,
                            },
                        ]
                    },
                }
            },
        },
    },
)
async def _(
    user_data: dict = Depends(verify_token),
):
    model_result = {}

    # 获取最新的官方模型列表
    async def get_newest_offical_model_list():
        try:
            model_list, next_cursor = await poe.client.explore_bot("Official")
        except Exception as e:
            return handle_exception(str(e))
        model_result["all"] = model_list

    # 获取支持diy（create bot）的模型并标记
    async def get_diy_model_list():
        try:
            result = await poe.client.send_query(
                "createBotIndexPageQuery",
                {"messageId": None},
            )
        except Exception as e:
            return handle_exception(str(e))
        model_result["diy"] = result["data"]["viewer"]["botsAllowedForUserCreation"]

    task1 = create_task(get_newest_offical_model_list())
    task2 = create_task(get_diy_model_list())
    await gather(task1, task2)

    for m in model_result["all"]:
        if m not in poe.client.offical_models:
            try:
                await poe.client.cache_offical_bot_info(m)
            except Exception as e:
                return handle_exception(str(e))

    for m in [_["displayName"] for _ in model_result["diy"]]:
        poe.client.offical_models[m].diy = True

    uid = user_data["uid"]

    level = await User.get_level(uid)

    data = []
    for model in model_result["all"]:
        info = poe.client.offical_models[model]
        # 普通用户不返回限制模型
        if info.limited and level == 1:
            continue
        data.append(
            {
                "model": model,
                "description": info.description,
                "diy": info.diy,
                "limited": info.limited,
            }
        )
    return JSONResponse({"available_models": data}, 200)


@router.get(
    "/list",
    summary="拉取用户可用会话",
    responses={
        200: {
            "description": "会话列表",
            "content": {
                "application/json": {
                    "example": {
                        "bots": [
                            {
                                "eop_id": "114514",
                                "alias": "AAA",
                                "model": "ChatGPT",
                                "prompt": "prompt_A",
                                "image": "https://xxx",
                                "create_time": 1693230928703,
                                "last_talk_time": 1693230928703,
                                "disable": False,
                            },
                            {
                                "eop_id": "415411",
                                "alias": "BBB",
                                "model": "ChatGPT4",
                                "prompt": "",
                                "image": "https://xxx",
                                "create_time": 1693230928703,
                                "last_talk_time": 1693230928703,
                                "disable": True,
                            },
                        ]
                    }
                }
            },
        },
    },
)
async def _(user_data: dict = Depends(verify_token)):
    uid = user_data["uid"]
    botList = await Bot.get_user_bot(uid)
    return JSONResponse({"bots": botList}, 200)


@router.post(
    "/create",
    summary="创建会话，prompt选填（不填留空），prompt仅支持diy的模型可用",
    responses={
        200: {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "example": {
                        "bot_info": {
                            "eop_id": "114514",
                            "alias": "AAA",
                            "model": "ChatGPT",
                            "prompt": "prompt_A",
                            "image": "https://xxx",
                            "create_time": 1693230928703,
                            "last_talk_time": 1693230928703,
                        },
                    }
                }
            },
        },
    },
)
async def _(
    body: CreateBody = Body(
        example={
            "model": "ChatGPT",
            "prompt": "",
            "alias": "新会话",
        }
    ),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]

    await check_user_outdate(uid)

    await check_user_level(uid, body.model)

    if body.model not in poe.client.offical_models:
        raise ModelNotFound(body.model)

    can_diy = poe.client.offical_models[body.model].diy

    try:
        # 如果是自定义prompt需要创建新的bot
        if can_diy and body.prompt:
            handle, bot_id = await poe.client.create_bot(
                poe.client.offical_models[body.model].model, body.prompt
            )
            can_diy = True
        else:
            handle, bot_id = (
                poe.client.offical_models[body.model].model,
                poe.client.offical_models[body.model].bot_id,
            )
            can_diy = False

        # 获取bot头像
        bot_data = await poe.client.get_bot_info(body.model)
        image_link = bot_data["image_link"]
        eop_id = await Bot.create_bot(
            uid,
            can_diy,
            handle,
            bot_id,
            body.model,
            body.alias,
            body.prompt,
            image_link,
        )
        user_logger.info(
            f"用户:{uid}  动作:创建会话  eop_id:{eop_id}  handle:{handle}（{body.model}）"
        )
        bot_info = await Bot.get_user_bot(uid, eop_id)
        return JSONResponse({"bot_info": bot_info[0]}, 200)

    except Exception as e:
        return handle_exception(str(e))


@router.post(
    "/{eop_id}/talk",
    summary="对话（提问）",
    responses={
        200: {
            "description": "回复内容，完毕type为end，出错type为error",
            "content": {
                "application/json": {
                    "example": {
                        "type": "response",
                        "data": "回答内容",
                    }
                }
            },
        }
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    body: TalkBody = Body(example={"q": "你好啊"}),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]

    handle, model, bot_id, chat_id, diy, disable = await Bot.get_bot_data(eop_id)

    # await check_user_outdate(uid)
    # await check_user_level(uid, model)
    # await check_bot_hoster(uid, eop_id)

    async def ai_reply():
        nonlocal chat_id
        # 判断账号过期
        if await User.is_outdate(uid):
            expire_date = await User.get_expire_date(uid)
            yield BytesIO(
                (
                    dumps(
                        {
                            "type": "expired",
                            "data": f"你的账号已过期，有效期至【{expire_date}】，无法对话",
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            ).read()
            return

        # 判断账号等级
        level = await User.get_level(uid)
        info = poe.client.offical_models[model]
        if info.limited and level == 1:
            yield BytesIO(
                (
                    dumps(
                        {
                            "type": "denied",
                            "data": f"你的账号等级不足，无法使用该模型对话",
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            ).read()
            return

        # 判断会话是否存在
        if not await Bot.check_bot_user(eop_id, uid):
            yield BytesIO(
                (
                    dumps(
                        {
                            "type": "deleted",
                            "data": "会话不存在",
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            ).read()
            return

        async for data in poe.client.talk_to_bot(handle, chat_id, body.q):
            # 会话失效
            if isinstance(data, SessionDisable):
                await Bot.disable_bot(eop_id)
                yield BytesIO(
                    (dumps({"type": "disable", "data": "该会话已失效，无法使用"}) + "\n").encode(
                        "utf-8"
                    )
                ).read()
            # 次数上限，有效性待测试
            if isinstance(data, ReachedLimit):
                yield BytesIO(
                    (dumps({"type": "limited", "data": "该模型使用次数已耗尽"}) + "\n").encode(
                        "utf-8"
                    )
                ).read()
            # 新的会话，需要保存chat code和chat id
            if isinstance(data, NewChat):
                chat_id = data.chat_id
                user_logger.info(
                    f"用户:{uid}  动作:新会话  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
                )
                await Bot.update_bot_chat_id(eop_id, chat_id)
            # 对话消息id和创建时间，用于同步
            if isinstance(data, MsgId):
                await Bot.update_bot_last_talk_time(eop_id, data.answer_create_time)
                yield BytesIO(
                    (
                        dumps(
                            {
                                "type": "start",
                                "data": {
                                    "question_msg_id": data.question_msg_id,
                                    "question_create_time": data.question_create_time,
                                    "answer_msg_id": data.answer_msg_id,
                                    "answer_create_time": data.answer_create_time,
                                },
                            }
                        )
                        + "\n"
                    ).encode("utf-8")
                ).read()
            # ai的回答
            if isinstance(data, Text):
                yield BytesIO(
                    (dumps({"type": "response", "data": data.content}) + "\n").encode(
                        "utf-8"
                    )
                ).read()
            # 回答完毕，更新最后对话时间
            if isinstance(data, End):
                user_logger.info(
                    f"用户:{uid}  动作:回答完毕  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
                )
                yield BytesIO((dumps({"type": "end"}) + "\n").encode("utf-8")).read()
            # 出错
            if isinstance(data, TalkError):
                user_logger.error(
                    f"用户:{uid}  动作:{data.content}  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
                )
                # 切换ws channel地址
                create_task(poe.client.refresh_channel())
                yield BytesIO(
                    (dumps({"type": "error", "data": data.content}) + "\n").encode(
                        "utf-8"
                    )
                ).read()

    return StreamingResponse(ai_reply(), media_type="text/event-stream")


@router.get(
    "/{eop_id}/stop",
    summary="停止生成回答",
    responses={
        200: {
            "description": "无相关响应",
        },
        204: {
            "description": "停止成功",
        },
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]
    await check_bot_hoster(uid, eop_id)

    handle, model, bot_id, chat_id, diy, disable = await Bot.get_bot_data(eop_id)
    await check_chat_exist(chat_id)

    try:
        await poe.client.talk_stop(handle, chat_id)
        user_logger.info(
            f"用户:{uid}  动作:停止回答  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
        )
        return Response(status_code=204)

    except Exception as e:
        return handle_exception(str(e))


@router.delete(
    "/{eop_id}",
    summary="删除会话",
    responses={
        200: {
            "description": "无相关响应",
        },
        204: {
            "description": "删除成功",
        },
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]
    await check_bot_hoster(uid, eop_id)

    handle, model, bot_id, chat_id, diy, disable = await Bot.get_bot_data(eop_id)

    try:
        if chat_id:
            await poe.client.delete_chat_by_chat_id(handle, chat_id)

        if diy and not disable:
            await poe.client.delete_bot(handle, bot_id)

    except Exception as e:
        return handle_exception(str(e))

    await Bot.delete_bot(eop_id)
    user_logger.info(
        f"用户:{uid}  动作:删除会话  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
    )
    return Response(status_code=204)


@router.delete(
    "/{eop_id}/reset",
    summary="重置对话，仅清除bot记忆，不会删除聊天记录",
    responses={
        200: {
            "description": "无相关响应",
        },
        204: {
            "description": "重置成功",
        },
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]
    await check_bot_hoster(uid, eop_id)

    handle, model, bot_id, chat_id, diy, disable = await Bot.get_bot_data(eop_id)
    await check_chat_exist(chat_id)

    try:
        await poe.client.send_chat_break(handle, chat_id)
        user_logger.info(
            f"用户:{uid}  动作:重置对话  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
        )
        return Response(status_code=204)

    except Exception as e:
        return handle_exception(str(e))


@router.delete(
    "/{eop_id}/clear",
    summary="重置对话并删除聊天记录",
    responses={
        200: {
            "description": "无相关响应",
        },
        204: {
            "description": "重置成功",
        },
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]
    await check_bot_hoster(uid, eop_id)

    handle, model, bot_id, chat_id, diy, disable = await Bot.get_bot_data(eop_id)
    await check_chat_exist(chat_id)

    try:
        await poe.client.delete_chat_by_chat_id(handle, chat_id)
        await Bot.update_bot_chat_id(eop_id)
        user_logger.info(
            f"用户:{uid}  动作:重置对话并删除聊天记录  eop_id:{eop_id}  handle:{handle}（{model}）  chat_id:{chat_id}"
        )
        return Response(status_code=204)

    except Exception as e:
        return handle_exception(str(e))


@router.get(
    "/{eop_id}/history/{cursor}",
    summary="拉取聊天记录",
    responses={
        200: {
            "description": "返回历史记录和翻页光标，如果next_cursor为-1，则没有下一页",
            "content": {
                "application/json": {
                    "example": {
                        "history": [
                            {
                                "msg_id": 2692997857,
                                "create_time": 1692964266475260,
                                "text": "你好啊",
                                "author": "user",
                            },
                            {
                                "msg_id": 2692997880,
                                "create_time": 1692964266638975,
                                "text": "你好啊！我是你的智能助手。有什么我可以帮助你的吗？",
                                "author": "bot",
                            },
                        ],
                        "next_cursor": "2692997857",
                    }
                }
            },
        }
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    cursor: str = Path(description="光标，用于翻页，写0则从最新的拉取", example="0"),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]
    await check_bot_hoster(uid, eop_id)

    handle, model, bot_id, chat_id, diy, disable = await Bot.get_bot_data(eop_id)
    if not chat_id:
        return JSONResponse(
            {
                "history": [],
                "next_cursor": -1,
            },
            200,
        )

    try:
        result_list, next_cursor = await poe.client.get_chat_history(
            handle, chat_id, cursor
        )

        return JSONResponse(
            {
                "history": result_list,
                "next_cursor": next_cursor,
            },
            200,
        )

    except Exception as e:
        return handle_exception(str(e))


@router.patch(
    "/{eop_id}",
    summary="修改bot信息，不改的就不提交，prompt如果为空的会话只能修改alias",
    responses={
        200: {
            "description": "无相关响应",
        },
        204: {
            "description": "成修改功",
        },
    },
)
async def _(
    eop_id: str = Path(description="会话唯一标识", example="114514"),
    body: ModifyBotBody = Body(
        example={
            "alias": "智能傻逼",
            "model": "ChatGPT",
            "prompt": "You are a large language model. Follow the user's instructions carefully.",
        }
    ),
    user_data: dict = Depends(verify_token),
):
    uid = user_data["uid"]
    await check_bot_hoster(uid, eop_id)

    if body.model and body.model not in poe.client.offical_models:
        raise ModelNotFound(body.model)

    # 更新缓存
    await Bot.modify_bot(eop_id, None, body.alias, None)

    handle, bot_id, diy = await Bot.pre_modify_bot_info(eop_id)
    # 只有支持diy的可以更新模型和预设
    if diy:
        # 更新缓存
        await Bot.modify_bot(eop_id, body.model, None, body.prompt)

        try:
            await poe.client.edit_bot(
                handle,
                bot_id,
                poe.client.offical_models[body.model].model,
                body.prompt,
            )
        except ServerError as e:
            # 判断bot是否被删了
            result = await poe.client.send_query(
                "HandleBotLandingPageQuery",
                {"botHandle": handle},
            )
            deletionState = result["data"]["bot"]["deletionState"]
            if deletionState != "not_deleted":
                await Bot.disable_bot(eop_id)
                raise BotDisable()

            return handle_exception(str(e))

        except Exception as e:
            return handle_exception(str(e))

    return Response(status_code=204)


@router.get(
    "/explore/{cursor}",
    summary="探索bot（todo）",
    responses={
        200: {
            "description": "返回历史记录和翻页光标，如果next_cursor为-1，则没有下一页",
            "content": {
                "application/json": {
                    "example": {
                        "history": [
                            {
                                "msg_id": 2692997857,
                                "create_time": 1692964266475260,
                                "text": "你好啊",
                                "author": "user",
                            },
                            {
                                "msg_id": 2692997880,
                                "create_time": 1692964266638975,
                                "text": "你好啊！我是你的智能助手。有什么我可以帮助你的吗？",
                                "author": "bot",
                            },
                        ],
                        "next_cursor": "2692997857",
                    }
                }
            },
        }
    },
)
async def _(
    cursor: str = Path(description="光标，用于翻页，写0则从最新的拉取", example=0),
    _: dict = Depends(verify_token),
):
    # handle, chat_id = await Bot.get_bot_handle_and_chat_id(eop_id)
    # if not chat_id:
    #     return JSONResponse(
    #         {
    #             "history": [],
    #             "next_cursor": -1,
    #         },
    #         200,
    #     )

    # try:
    #     result_list, next_cursor = await poe.client.get_chat_history(
    #         handle, chat_id, cursor
    #     )

    #     return JSONResponse(
    #         {
    #             "history": result_list,
    #             "next_cursor": next_cursor,
    #         },
    #         200,
    #     )

    # except Exception as e:
    #     return handle_exception(str(e))
    pass
