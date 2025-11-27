# api/endpoints/topics.py (最终修复版)

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.templating import Jinja2Templates

from core.database import get_db
from crud import crud_operations
from schemas.base_schemas import TopicCreate

templates = Jinja2Templates(directory="templates")
router = APIRouter()

# --- 页面路由 ---

@router.get("/topics", response_class=HTMLResponse)
async def page_manage_topics(request: Request, db: AsyncSession = Depends(get_db)):
    """渲染“议题管理”主页面"""
    topics = await crud_operations.get_topics(db)
    return templates.TemplateResponse("topics.html", {"request": request, "topics": topics})

# --- 这是你缺失的关键路由 ---
@router.get("/topics/{topic_id}/edit", response_class=HTMLResponse)
async def page_edit_topic(request: Request, topic_id: int, db: AsyncSession = Depends(get_db)):
    """渲染单个议题的编辑页面"""
    topic = await crud_operations.get_topic(db, topic_id=topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return templates.TemplateResponse("topic_edit.html", {"request": request, "topic": topic})
# --- 路由添加结束 ---


# --- API 路由 ---

@router.post("/api/topics", response_class=HTMLResponse)
async def api_create_topic(request: Request, name: str = Form(...), description: str = Form(""), db: AsyncSession = Depends(get_db)):
    """创建新议题，并返回更新后的议题列表"""
    await crud_operations.create_topic(db, topic=TopicCreate(name=name, description=description))
    topics = await crud_operations.get_topics(db)
    return templates.TemplateResponse("partials/topic_list.html", {"request": request, "topics": topics})

@router.get("/api/topics", response_class=HTMLResponse)
async def api_get_topics(request: Request, db: AsyncSession = Depends(get_db)):
    """获取所有议题列表的HTML片段"""
    topics = await crud_operations.get_topics(db)
    return templates.TemplateResponse("partials/topic_list.html", {"request": request, "topics": topics})

@router.post("/api/topics/{topic_id}/edit")
async def api_update_topic(
    topic_id: int,
    name: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    """处理编辑议题的表单提交"""
    await crud_operations.update_topic(db, topic_id=topic_id, name=name, description=description)
    return HTMLResponse("<div class='alert alert-success'>议题已成功更新！ <a href='/topics' class='alert-link'>返回列表</a></div>")

@router.delete("/api/topics/{topic_id}", response_class=Response)
async def api_delete_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    """处理删除议题的请求"""
    await crud_operations.delete_topic(db, topic_id=topic_id)
    return Response(status_code=200, headers={"HX-Trigger": "loadTopics"})