# api/endpoints/clients.py

import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from typing import List

from core.database import get_db
from crud import crud_operations
from schemas.base_schemas import ClientSchema, ClientCreate

# 确保每个API文件都有自己的templates实例，以避免循环依赖
templates = Jinja2Templates(directory="templates")

# --- 定义两个独立的Router ---
# api_router 负责处理数据或HTML片段的请求
api_router = APIRouter(prefix="/api/clients", tags=["Clients API"])
# pages_router 负责渲染完整的HTML页面
pages_router = APIRouter(tags=["Pages"])

# 队列文件路径
QUEUE_FILE = Path("job_queue.txt")


# --- 页面路由 ---

@pages_router.get("/clients", response_class=HTMLResponse)
async def page_clients(request: Request):
    """渲染"客户端管理"主页面"""
    return templates.TemplateResponse("clients.html", {"request": request})


@pages_router.get("/clients/{client_id}", response_class=HTMLResponse)
async def page_client_detail(request: Request, client_id: int, db: AsyncSession = Depends(get_db)):
    """渲染"客户端详情与订阅管理"页面"""
    # 获取客户端信息
    client = await crud_operations.get_client(db, client_id=client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return templates.TemplateResponse("client_detail.html",
                                      {"request": request, "client_id": client_id, "client": client})


# --- API 路由 ---

@api_router.post("/", response_class=HTMLResponse)
async def api_create_client(
        request: Request,
        name: str = Form(...),
        description: str = Form(""),
        db: AsyncSession = Depends(get_db)
):
    """处理添加新客户端的请求，并返回新创建的那一行的HTML片段。"""
    try:
        # 检查客户端是否已存在
        existing_client = await crud_operations.get_client_by_name(db, name=name)
        if existing_client:
            # 返回一个错误提示的HTML片段
            return HTMLResponse(
                f'<tr><td colspan="4"><div class="alert alert-warning">客户端 \'{name}\' 已存在。</div></td></tr>',
                status_code=400
            )

        new_client = await crud_operations.create_client(db, client=ClientCreate(name=name, description=description))
        # htmx 会将这个渲染好的 tr 插入到表格中
        return templates.TemplateResponse("partials/client_row.html", {"request": request, "client": new_client})
    except Exception as e:
        return HTMLResponse(
            f'<tr><td colspan="4"><div class="alert alert-danger">创建客户端失败: {str(e)}</div></td></tr>',
            status_code=500
        )


@api_router.get("/", response_class=HTMLResponse)
async def api_get_clients(request: Request, db: AsyncSession = Depends(get_db)):
    """获取所有客户端，并以一个完整的表格HTML片段返回。"""
    try:
        clients = await crud_operations.get_clients(db)
        return templates.TemplateResponse("partials/client_list.html", {"request": request, "clients": clients})
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-danger">加载客户端列表失败: {str(e)}</div>', status_code=500)


@api_router.post("/{client_id}/collect")
async def api_collect_for_client(client_id: int, db: AsyncSession = Depends(get_db)):
    """将一个"为客户收集情报"的任务添加到队列中。"""
    try:
        client = await crud_operations.get_client(db, client_id=client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        task = {"task_type": "collect_intelligence", "payload": client_id}

        # 向任务队列文件追加任务
        try:
            with open(QUEUE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"写入队列文件失败: {str(e)}")

        return {"message": f"任务已提交后台处理：为客户 '{client.name}' 收集情报。"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"提交任务失败: {str(e)}")


@api_router.delete("/{client_id}", response_class=Response)
async def api_delete_client(client_id: int, db: AsyncSession = Depends(get_db)):
    """删除客户端"""
    try:
        client = await crud_operations.get_client(db, client_id=client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        await crud_operations.delete_client(db, client_id=client_id)
        return Response(status_code=200)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@api_router.get("/{client_id}/subscriptions", response_class=HTMLResponse)
async def api_get_client_subscriptions(request: Request, client_id: int, db: AsyncSession = Depends(get_db)):
    """获取客户端订阅的议题列表，并提供所有可供选择的议题"""
    try:
        # 1. 使用get_client 函数，它会通过selectinload同时加载关联的topics
        client = await crud_operations.get_client(db, client_id=client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # 2. 客户端已订阅的议题，可以直接从 client.topics 属性获得
        subscribed_topics = client.topics

        # 3. 获取所有议题，用于"添加订阅"模态框的下拉列表
        all_topics = await crud_operations.get_topics(db)

        # 4. 优化体验：从所有议题中，排除掉客户已经订阅的
        subscribed_topic_ids = {topic.id for topic in subscribed_topics}
        available_topics = [topic for topic in all_topics if topic.id not in subscribed_topic_ids]

        # 5. 将客户已订阅的议题列表 (subscribed_topics) 传递给模板
        return templates.TemplateResponse(
            "partials/client_subscriptions.html",
            {
                "request": request,
                "client": client,
                "subscriptions": subscribed_topics, # 传递的是 Topic 对象列表
                "available_topics": available_topics
            }
        )
    except Exception as e:
        # 增强的错误处理
        logging.error(f"加载订阅失败 (client_id: {client_id})", exc_info=True)
        return HTMLResponse(
            f'<div class="alert alert-danger">加载订阅信息失败: <pre>{str(e)}</pre></div>',
            status_code=500
        )


@api_router.post("/{client_id}/subscriptions", response_class=HTMLResponse)
async def api_add_subscription_for_client(
    request: Request,
    client_id: int,
    topic_id: int = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """为客户端添加一个新的议题订阅"""
    await crud_operations.assign_topic_to_client(db, client_id=client_id, topic_id=topic_id)
    return await api_get_client_subscriptions(request, client_id, db)


@api_router.delete("/{client_id}/topics/{topic_id}", response_class=Response)
async def api_remove_subscription_for_client(
    client_id: int,
    topic_id: int,
    db: AsyncSession = Depends(get_db)
):
    """为客户端取消一个议题订阅"""
    await crud_operations.unassign_topic_from_client(db, client_id=client_id, topic_id=topic_id)
    return Response(status_code=200)
