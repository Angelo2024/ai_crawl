# api/endpoints/intelligence.py - 完整修复版
from __future__ import annotations

import json
import html
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import time
import csv
import json
import io
from datetime import datetime

# FastAPI 核心导入
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Form
from starlette.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# SQLAlchemy 导入
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_, or_, func, text
from sqlalchemy.orm import selectinload

# 项目内部导入
from core.database import get_db, get_ai_processing_db
from models.intelligence_models import Intelligence, IntelligenceSource
from models.base_models import Topic, Source
from schemas.intelligence_schemas import (
    IntelligenceCreate, IntelligenceUpdate, IntelligenceResponse,
    IntelligenceFilter, IntelligenceScore, MergeRequest
)

# 创建路由器
api_router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])
pages_router = APIRouter(prefix="/intelligence", tags=["intelligence_pages"])

# 配置模板目录
templates = Jinja2Templates(directory="templates")


# ===== API路由 =====
def safe_format_datetime(dt_value):
    """安全地格式化时间值"""
    if dt_value is None:
        return ""
    try:
        if isinstance(dt_value, str):
            return dt_value
        elif hasattr(dt_value, 'isoformat'):
            return dt_value.isoformat()
        else:
            return str(dt_value)
    except Exception:
        return str(dt_value) if dt_value else ""


@api_router.get("/export")
async def export_intelligence(
        db: AsyncSession = Depends(get_db),
        format: str = Query("csv", description="导出格式: csv, json, excel"),
        export_scope: str = Query("selected", description="导出范围: selected, filtered, all"),
        intelligence_ids: Optional[str] = Query(None, description="选中的情报ID列表(逗号分隔)"),
        # 筛选参数
        title: Optional[str] = Query(None),
        topic: Optional[str] = Query(None),
        quality: Optional[str] = Query(None),
        min_score: Optional[float] = Query(None),
        max_score: Optional[float] = Query(None),
        news_start_date: Optional[str] = Query(None),
        news_end_date: Optional[str] = Query(None),
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None)
):
    """导出情报数据"""
    try:
        print(f"导出请求: format={format}, scope={export_scope}")

        # 根据导出范围构建查询
        data_to_export = []

        if export_scope == "selected" and intelligence_ids:
            # 导出选中的情报
            ids = [int(id.strip()) for id in intelligence_ids.split(',') if id.strip()]
            if not ids:
                raise HTTPException(status_code=400, detail="未提供有效的情报ID")

            from sqlalchemy import text
            placeholders = ','.join([f':id_{j}' for j in range(len(ids))])
            params = {f'id_{j}': ids[j] for j in range(len(ids))}

            # 添加 score_dimensions 字段
            query_sql = f"""
                SELECT i.id, i.title, i.summary, i.topic, i.news_time, i.collect_time, 
                       i.ai_score, i.score_dimensions, i.quality_status, i.is_merged, i.merged_count,
                       s.url, s.domain
                FROM intelligence i
                LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
                WHERE i.id IN ({placeholders})
                ORDER BY i.news_time DESC
            """

            result = await db.execute(text(query_sql), params)
            rows = result.fetchall()

        elif export_scope == "filtered":
            # 导出筛选结果
            where_conditions = []
            params = {}

            if title and title.strip():
                where_conditions.append("i.title LIKE :title")
                params['title'] = f"%{title.strip()}%"

            if topic and topic.strip():
                where_conditions.append("i.topic LIKE :topic")
                params['topic'] = f"%{topic.strip()}%"

            if quality and quality.strip():
                where_conditions.append("i.quality_status = :quality")
                params['quality'] = quality.strip()

            if min_score is not None:
                where_conditions.append("i.ai_score >= :min_score")
                params['min_score'] = float(min_score)

            if max_score is not None:
                where_conditions.append("i.ai_score <= :max_score")
                params['max_score'] = float(max_score)

            # 时间筛选
            if news_start_date:
                try:
                    from dateutil import parser
                    start_dt = parser.parse(news_start_date)
                    where_conditions.append("i.news_time >= :news_start_date")
                    params['news_start_date'] = start_dt
                except:
                    pass

            if news_end_date:
                try:
                    from dateutil import parser
                    end_dt = parser.parse(news_end_date)
                    where_conditions.append("i.news_time <= :news_end_date")
                    params['news_end_date'] = end_dt
                except:
                    pass

            # 构建WHERE子句
            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)

            # 添加 score_dimensions 字段
            query_sql = f"""
                SELECT i.id, i.title, i.summary, i.topic, i.news_time, i.collect_time, 
                       i.ai_score, i.score_dimensions, i.quality_status, i.is_merged, i.merged_count,
                       s.url, s.domain
                FROM intelligence i
                LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
                {where_clause}
                ORDER BY i.news_time DESC NULLS LAST
                LIMIT 10000
            """

            result = await db.execute(text(query_sql), params)
            rows = result.fetchall()

        else:  # export_scope == "all"
            # 导出全部数据
            query_sql = """
                SELECT i.id, i.title, i.summary, i.topic, i.news_time, i.collect_time, 
                       i.ai_score, i.score_dimensions, i.quality_status, i.is_merged, i.merged_count,
                       s.url, s.domain
                FROM intelligence i
                LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
                ORDER BY i.news_time DESC NULLS LAST
                LIMIT 50000
            """

            result = await db.execute(text(query_sql), {})
            rows = result.fetchall()

        # 处理查询结果
        intelligence_dict = {}
        for row in rows:
            intel_id = row.id
            if intel_id not in intelligence_dict:
                # 解析评分数据
                dimensions = {}
                if row.score_dimensions:
                    try:
                        import json
                        if isinstance(row.score_dimensions, str):
                            dimensions = json.loads(row.score_dimensions)
                        else:
                            dimensions = row.score_dimensions
                        print(f"成功解析情报 {intel_id} 的评分数据: {dimensions}")
                    except Exception as json_error:
                        print(f"JSON解析失败 {intel_id}: {json_error}")
                        dimensions = {}

                intelligence_dict[intel_id] = {
                    'id': row.id,
                    'title': row.title,
                    'summary': row.summary or '',
                    'topic': row.topic or '',
                    'category': '前沿资讯',  # 默认类别
                    'news_time': safe_format_datetime(row.news_time),
                    'collect_time': safe_format_datetime(row.collect_time),
                    'ai_score': float(row.ai_score or 0),
                    'dimensions': dimensions,  # 添加解析后的评分数据
                    'quality_status': row.quality_status or 'pending',
                    'is_merged': bool(row.is_merged),
                    'merged_count': int(row.merged_count or 0),
                    'sources': []
                }

            # 添加来源信息
            if row.url:
                intelligence_dict[intel_id]['sources'].append({
                    'url': row.url,
                    'domain': row.domain or ''
                })

        data_to_export = list(intelligence_dict.values())

        print(f"准备导出 {len(data_to_export)} 条情报")

        # 根据格式生成相应的响应
        if format.lower() == 'csv':
            return export_as_csv(data_to_export)
        elif format.lower() == 'json':
            return export_as_json(data_to_export)
        elif format.lower() == 'excel':
            return export_as_excel(data_to_export)
        else:
            raise HTTPException(status_code=400, detail="不支持的导出格式")

    except Exception as e:
        print(f"导出失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


def export_as_csv(data):
    """导出为CSV格式 - 支持新标题"""
    output = io.StringIO()
    writer = csv.writer(output)

    # 写入表头 - 添加新标题字段
    headers = [
        'ID', '原标题', '新标题', '摘要', '议题', '类别', '新闻时间', '收集时间',
        'AI评分', '战略相关性评分', '行业影响力评分', '时效性紧迫性评分',
        '业务机会风险强度评分', '可操作性评分', '评分细则汇总',
        '质量状态', '是否合并', '合并数量', '来源链接', '来源域名'
    ]
    writer.writerow(headers)

    # 写入数据
    for item in data:
        print(f"处理导出数据 {item['id']}: dimensions = {item.get('dimensions', {})}")

        # 处理多个来源
        sources_urls = []
        sources_domains = []

        for source in item.get('sources', []):
            sources_urls.append(source.get('url', ''))
            sources_domains.append(source.get('domain', ''))

        # 处理新的评分维度
        dimensions = item.get('dimensions', {})
        score_details = format_score_details(dimensions)

        # 提取各维度评分
        strategic_score = 0
        industry_score = 0
        timeliness_score = 0
        business_score = 0
        actionability_score = 0

        if dimensions:
            strategic_data = dimensions.get('战略相关性', {})
            if isinstance(strategic_data, dict):
                strategic_score = strategic_data.get('分数', 0)

            industry_data = dimensions.get('行业影响力', {})
            if isinstance(industry_data, dict):
                industry_score = industry_data.get('分数', 0)

            timeliness_data = dimensions.get('时效性紧迫性', {})
            if isinstance(timeliness_data, dict):
                timeliness_score = timeliness_data.get('分数', 0)

            business_data = dimensions.get('业务机会风险强度', {})
            if isinstance(business_data, dict):
                business_score = business_data.get('分数', 0)

            actionability_data = dimensions.get('可操作性', {})
            if isinstance(actionability_data, dict):
                actionability_score = actionability_data.get('分数', 0)

        row = [
            item.get('id', ''),
            item.get('original_title', item.get('title', '')),  # 原标题
            item.get('new_title', ''),  # 新标题，如果没有则为空
            item.get('summary', ''),
            item.get('topic', ''),
            item.get('category', '前沿资讯'),
            item.get('news_time', ''),
            item.get('collect_time', ''),
            item.get('ai_score', 0),
            strategic_score,
            industry_score,
            timeliness_score,
            business_score,
            actionability_score,
            score_details,
            item.get('quality_status', ''),
            '是' if item.get('is_merged') else '否',
            item.get('merged_count', 0),
            '; '.join(sources_urls),
            '; '.join(sources_domains)
        ]
        writer.writerow(row)

    output.seek(0)

    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"intelligence_export_{timestamp}.csv"

    # 返回响应
    response = StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        media_type='text/csv',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

    return response


def export_as_json(data):
    """导出为JSON格式"""
    # 为每个项目添加格式化的评分细则
    for item in data:
        item['formatted_score_details'] = format_score_details(item.get('dimensions', {}))

    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"intelligence_export_{timestamp}.json"

    # 构建导出数据结构
    export_data = {
        "export_info": {
            "timestamp": datetime.now().isoformat(),
            "total_records": len(data),
            "format": "JSON"
        },
        "data": data
    }

    json_content = json.dumps(export_data, ensure_ascii=False, indent=2)

    response = StreamingResponse(
        io.BytesIO(json_content.encode('utf-8')),
        media_type='application/json',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

    return response


def export_as_excel(data):
    """导出为Excel格式"""
    output = io.StringIO()
    writer = csv.writer(output)

    # 写入表头
    headers = [
        'ID', '标题', '摘要', '议题', '类别', '新闻时间', '收集时间',
        'AI评分', '评分细则', '质量状态', '是否合并', '合并数量', '来源链接数量'
    ]
    writer.writerow(headers)

    # 写入数据
    for item in data:
        score_details = format_score_details(item.get('dimensions', {}))

        row = [
            item.get('id', ''),
            item.get('title', ''),
            item.get('summary', ''),
            item.get('topic', ''),
            item.get('category', '前沿资讯'),
            item.get('news_time', ''),
            item.get('collect_time', ''),
            item.get('ai_score', 0),
            score_details,
            item.get('quality_status', ''),
            '是' if item.get('is_merged') else '否',
            item.get('merged_count', 0),
            len(item.get('sources', []))
        ]
        writer.writerow(row)

    output.seek(0)

    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"intelligence_export_{timestamp}.csv"

    response = StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        media_type='application/vnd.ms-excel',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

    return response


def format_score_details(dimensions):
    """格式化评分细则为单个字符串 - 支持新的评分维度"""
    if not dimensions or not isinstance(dimensions, dict):
        return "暂无评分细则"

    print(f"格式化评分细则，输入: {dimensions}")

    details = []

    # 处理新的评分维度
    dimension_mapping = {
        "战略相关性": "strategic_relevance",
        "行业影响力": "industry_impact",
        "时效性紧迫性": "timeliness_urgency",
        "业务机会风险强度": "business_opportunity_risk",
        "可操作性": "actionability"
    }

    for chinese_name, english_key in dimension_mapping.items():
        # 优先使用中文键，其次使用英文键
        dimension_data = dimensions.get(chinese_name, dimensions.get(english_key, {}))

        if isinstance(dimension_data, dict):
            score = dimension_data.get('分数', dimension_data.get('score', 0))
            reason = dimension_data.get('理由', dimension_data.get('reason', '')).strip()

            if reason:
                details.append(f"{chinese_name}({score}/10): {reason}")

    # 如果没有找到任何有效的评分细则，返回默认值
    if not details:
        print(f"未找到有效评分细则，原始数据: {dimensions}")
        return "暂无评分细则"

    result = '; '.join(details)
    print(f"格式化结果: {result}")
    return result


def build_score_tooltip(dimensions):
    """构建AI评分tooltip - 支持新评分维度"""
    tooltip_content = "暂无AI评分详情"

    if dimensions:
        tooltip_parts = []

        # 新的评分维度
        dimension_configs = [
            ("战略相关性", "strategic_relevance"),
            ("行业影响力", "industry_impact"),
            ("时效性紧迫性", "timeliness_urgency"),
            ("业务机会风险强度", "business_opportunity_risk"),
            ("可操作性", "actionability")
        ]

        for chinese_name, english_key in dimension_configs:
            dimension_data = dimensions.get(chinese_name, dimensions.get(english_key, {}))

            if isinstance(dimension_data, dict):
                score_val = dimension_data.get('分数', dimension_data.get('score', 0))
                reason = dimension_data.get('理由', dimension_data.get('reason', ''))

                # 限制理由长度避免tooltip过长
                if len(reason) > 100:
                    reason = reason[:100] + '...'

                if reason:
                    tooltip_parts.append(f"{chinese_name}: {score_val}/10 - {reason}")

        if tooltip_parts:
            tooltip_content = "\\n".join(tooltip_parts)

    return html.escape(tooltip_content).replace('"', '&quot;').replace("'", '&#39;')


@api_router.get("/export-template")
async def download_template():
    """下载导入模板 - 包含类别和评分细则字段"""
    try:
        output = io.StringIO()
        writer = csv.writer(output)

        # 模板表头 - 包含类别字段
        headers = [
            'title', 'summary', 'topic', 'category', 'news_time',
            'source_url', 'source_title', 'quality_status'
        ]
        writer.writerow(headers)

        # 示例数据
        example_row = [
            '示例新闻标题',
            '这是一个示例摘要，描述新闻的主要内容',
            'ESG',
            '前沿资讯',  # 添加类别示例
            '2024-01-15 10:30:00',
            'https://example.com/news/123',
            '示例新闻网站',
            'pending'
        ]
        writer.writerow(example_row)

        output.seek(0)

        response = StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            media_type='text/csv',
            headers={"Content-Disposition": "attachment; filename=intelligence_import_template.csv"}
        )

        return response

    except Exception as e:
        print(f"❌ 模板下载失败: {e}")
        raise HTTPException(status_code=500, detail=f"模板下载失败: {str(e)}")


@api_router.post("/batch-extract-dates")
async def batch_extract_dates(
        db: AsyncSession = Depends(get_db),
        request_data: dict = Body(...)
):
    """批量为缺少日期的文章提取日期 - SQLite兼容版"""
    try:
        intelligence_ids = request_data.get("intelligence_ids", [])
        date_selectors = request_data.get("date_selectors", [])

        if not intelligence_ids:
            return {"status": "error", "message": "请提供要处理的情报ID列表"}

        print(f"🕒 开始批量日期提取: {len(intelligence_ids)} 条情报")

        # 修复：使用命名参数而不是位置参数
        from sqlalchemy import text

        # 构建查询 - 分批处理大量ID
        articles_data_all = []
        batch_size = 100  # SQLite对IN子句有限制

        for i in range(0, len(intelligence_ids), batch_size):
            batch_ids = intelligence_ids[i:i + batch_size]

            # 修复：构建命名参数
            placeholders = ','.join([f':id_{j}' for j in range(len(batch_ids))])
            params = {f'id_{j}': batch_ids[j] for j in range(len(batch_ids))}

            query_sql = f"""
                SELECT i.id, i.title, i.news_time, s.url, s.title as source_title
                FROM intelligence i
                LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
                WHERE i.id IN ({placeholders})
            """

            # 使用字典参数而不是元组
            result = await db.execute(text(query_sql), params)
            articles_data_all.extend(result.fetchall())

        if not articles_data_all:
            return {"status": "error", "message": "未找到指定的情报"}

        # 转换为处理格式
        articles = []
        for row in articles_data_all:
            articles.append({
                'id': row.id,
                'title': row.title,
                'news_time': row.news_time,
                'url': row.url
            })

        # 调用批量日期提取功能
        from services.ai_service import batch_extract_missing_dates

        extraction_results = await batch_extract_missing_dates(articles, date_selectors)

        # 更新数据库中成功提取到日期的记录
        updated_count = 0
        successful_results = []

        for result in extraction_results:
            if result['success'] and result.get('extracted_date'):
                try:
                    await db.execute(
                        text("""
                            UPDATE intelligence 
                            SET news_time = :news_time, 
                                update_time = :update_time
                            WHERE id = :id
                        """),
                        {
                            "id": result['id'],
                            "news_time": result['extracted_date'],
                            "update_time": datetime.now().isoformat()
                        }
                    )
                    updated_count += 1
                    successful_results.append(result)
                    print(f"✅ 已更新情报 {result['id']} 的日期: {result['extracted_date']}")
                except Exception as e:
                    print(f"❌ 更新情报 {result['id']} 失败: {e}")
                    result['success'] = False
                    result['error'] = str(e)

        await db.commit()

        total_processed = len(extraction_results)
        success_count = len(successful_results)

        print(f"🎉 批量日期提取完成: 成功 {success_count}/{total_processed} 条")

        return {
            "status": "success",
            "message": f"批量日期提取完成: 成功 {success_count} 条，失败 {total_processed - success_count} 条",
            "total_processed": total_processed,
            "success_count": success_count,
            "updated_count": updated_count,
            "results": extraction_results,
            "successful_extractions": successful_results
        }

    except Exception as e:
        print(f"❌ 批量日期提取失败: {e}")
        import traceback
        traceback.print_exc()
        await db.rollback()
        return {"status": "error", "message": f"批量日期提取失败: {str(e)}"}


@api_router.get("/missing-dates")
async def get_articles_missing_dates(
        db: AsyncSession = Depends(get_db),
        limit: int = Query(100, ge=1, le=500, description="限制返回数量"),
        topic: Optional[str] = Query(None, description="按议题筛选")
):
    """获取缺少日期的文章列表 - SQLite兼容版"""
    try:
        print(f"收到请求参数: limit={limit}, topic={topic}")

        from sqlalchemy import text

        # SQLite兼容的查询
        query_sql = """
            SELECT i.id, i.title, i.topic, i.collect_time, s.url, s.domain
            FROM intelligence i
            LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
            WHERE (i.news_time IS NULL OR i.news_time = '')
        """

        params = {'limit': limit}  # 始终使用字典参数

        # 添加topic筛选条件
        if topic and topic.strip():
            query_sql += " AND i.topic LIKE :topic"
            params['topic'] = f"%{topic.strip()}%"

        # 添加排序和限制
        query_sql += " ORDER BY i.collect_time DESC LIMIT :limit"

        print(f"执行SQL: {query_sql}")
        print(f"SQL参数: {params}")

        result = await db.execute(text(query_sql), params)
        rows = result.fetchall()

        articles = []
        seen_ids = set()  # 去重

        for row in rows:
            if row.id not in seen_ids:
                seen_ids.add(row.id)
                articles.append({
                    "id": row.id,
                    "title": row.title,
                    "topic": row.topic or "未分类",
                    "collect_time": row.collect_time.isoformat() if row.collect_time else None,
                    "url": row.url or "",
                    "domain": row.domain or ""
                })

        print(f"找到 {len(articles)} 条缺少日期的文章")

        return {
            "status": "success",
            "total": len(articles),
            "articles": articles
        }

    except Exception as e:
        print(f"查询缺少日期的文章失败: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
        return {"status": "error", "message": str(e)}


@api_router.post("/test-date-extraction")
async def test_date_extraction(
        request_data: dict = Body(...)
):
    """测试日期提取功能"""
    try:
        url = request_data.get("url")
        date_selectors = request_data.get("date_selectors", [])

        if not url:
            return {"status": "error", "message": "请提供要测试的URL"}

        print(f"🧪 测试日期提取: {url}")

        from services.ai_service import extract_date_from_url

        result = await extract_date_from_url(url, date_selectors)

        return {
            "status": "success",
            "url": url,
            "extraction_result": {
                "success": result.success,
                "date": result.date,
                "confidence": result.confidence,
                "method": result.method,
                "error_message": result.error_message
            }
        }

    except Exception as e:
        print(f"❌ 日期提取测试失败: {e}")
        return {"status": "error", "message": str(e)}


# 修改现有的AI处理函数，支持日期提取
async def ai_process_single_intelligence_with_date_extraction(
        intelligence_id: int,
        db: AsyncSession,
        date_selectors: List[str] = None
):
    """单个情报AI处理 - 避免session冲突版"""
    try:
        print(f"🤖 开始AI分析情报 ID: {intelligence_id}")

        # 使用原生SQL获取情报和来源，提高性能
        from sqlalchemy import text
        result = await db.execute(
            text("""
                SELECT i.id, i.title, i.summary, i.topic, i.news_time, i.content,
                       s.url, s.title as source_title, s.domain
                FROM intelligence i
                LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
                WHERE i.id = :id
                LIMIT 1
            """),
            {"id": intelligence_id}
        )

        row = result.fetchone()
        if not row:
            return {"status": "error", "message": "情报不存在"}

        # 构建NewsArticle对象
        from services.ai_service import analyze_article_with_deepseek, NewsArticle

        def safe_format_datetime(dt_value):
            if dt_value is None:
                return ""
            try:
                if isinstance(dt_value, str):
                    return dt_value
                elif hasattr(dt_value, 'isoformat'):
                    return dt_value.isoformat()
                else:
                    return str(dt_value)
            except Exception:
                return str(dt_value) if dt_value else ""

        article = NewsArticle(
            source=row.domain or "unknown",
            title=row.title,
            url=row.url or "",
            publish_date=safe_format_datetime(row.news_time),
            content=row.content or row.summary or row.title,
            content_hash=""
        )

        print(f"📄 准备分析文章: {article.title[:50]}...")

        # 关键修复：在AI分析前提交当前事务，避免冲突
        await db.commit()

        # 调用AI分析，增加超时控制
        analysis = await asyncio.wait_for(
            analyze_article_with_deepseek(article, date_selectors=date_selectors),
            timeout=45  # 增加到45秒超时
        )

        print(f"🎯 AI分析完成")

        # 计算综合评分 - 使用新的权重系统
        scores = analysis.get("评分详情", {})
        weights = {
            "战略相关性": 0.30,
            "行业影响力": 0.20,
            "时效性紧迫性": 0.20,
            "业务机会风险强度": 0.15,
            "可操作性": 0.15
        }

        total_score = 0
        for dimension, weight in weights.items():
            score_data = scores.get(dimension, {})
            if isinstance(score_data, dict) and "分数" in score_data:
                total_score += score_data["分数"] * weight

        # 准备更新的字段
        update_data = {
            "id": intelligence_id,
            "topic": analysis.get("议题", "未分类"),
            "summary": analysis.get("摘要", row.title),
            "ai_score": round(total_score, 1),
            "score_dimensions": json.dumps(scores, ensure_ascii=False),
            "update_time": datetime.now().isoformat()
        }

        # 关键新功能：更新标题
        new_title = analysis.get("新标题")
        if new_title and new_title.strip() and new_title != row.title:
            update_data["title"] = new_title.strip()
            print(f"📝 标题将更新: {row.title[:30]}... → {new_title[:30]}...")

        # 检查是否提取到了新的日期
        meta_info = analysis.get("_meta", {})
        if meta_info.get("date_extracted") and meta_info.get("extracted_date"):
            update_data["news_time"] = meta_info["extracted_date"]
            print(f"📅 同时更新提取到的日期: {meta_info['extracted_date']}")

        # 更新数据库 - 使用新的session
        import json

        # 构建动态SQL更新语句
        set_clauses = []
        for key in update_data.keys():
            if key != "id":
                set_clauses.append(f"{key} = :{key}")

        update_sql = f"""
            UPDATE intelligence 
            SET {', '.join(set_clauses)}
            WHERE id = :id
        """

        await db.execute(text(update_sql), update_data)
        await db.commit()

        result_data = {
            "status": "success",
            "ai_score": round(total_score, 1),
            "dimensions": scores,
            "topic": analysis.get("议题"),
            "summary": analysis.get("摘要"),
            "category": analysis.get("类别")
        }

        # 如果更新了标题，添加到结果中
        if "title" in update_data:
            result_data["new_title"] = update_data["title"]
            result_data["original_title"] = row.title

        # 如果提取到了日期，添加到结果中
        if meta_info.get("date_extracted"):
            result_data["extracted_date"] = meta_info["extracted_date"]
            result_data["date_extraction_method"] = meta_info.get("date_extraction_method")
            result_data["date_confidence"] = meta_info.get("date_confidence")

        return result_data

    except asyncio.TimeoutError:
        print(f"⏰ AI分析超时: 情报 {intelligence_id}")
        return {"status": "error", "message": "AI分析超时"}
    except Exception as e:
        print(f"❌ AI分析失败: {e}")
        import traceback
        traceback.print_exc()
        await db.rollback()  # 回滚事务
        return {"status": "error", "message": f"AI分析失败: {str(e)}"}


# 修改批量AI处理，支持日期提取
@api_router.post("/batch-ai-process-with-dates")
async def batch_ai_process_with_date_extraction(
        db: AsyncSession = Depends(get_db),
        request_data: dict = Body(...)
):
    """批量AI处理 - 完全隔离session版本"""
    try:
        intelligence_ids = request_data.get("intelligence_ids", [])
        date_selectors = request_data.get("date_selectors", [])

        if not intelligence_ids:
            return {"status": "error", "message": "请提供要处理的情报ID列表"}

        print(f"🚀 开始批量AI分析(含日期提取): {len(intelligence_ids)} 条情报")

        # 关键修复：确保完全串行，每个任务独立session
        results = []
        success_count = 0
        start_time = time.time()

        for idx, intel_id in enumerate(intelligence_ids, 1):
            try:
                print(f"🤖 处理情报 {intel_id} ({idx}/{len(intelligence_ids)})...")

                # 关键修复：每个任务完全独立的session
                result = await process_single_intelligence_isolated(intel_id, date_selectors)
                results.append({"id": intel_id, **result})

                if result["status"] == "success":
                    success_count += 1
                    date_info = ""
                    if result.get("extracted_date"):
                        date_info = f"，日期: {result['extracted_date']}"
                    print(f"✅ 情报 {intel_id} 分析成功，评分: {result.get('ai_score', 'N/A')}{date_info}")
                else:
                    print(f"⚠️ 情报 {intel_id} 分析失败: {result['message']}")

                # 增加延迟，避免过快请求
                if idx < len(intelligence_ids):
                    await asyncio.sleep(0.5)  # 增加到0.5秒

            except Exception as e:
                print(f"❌ 情报 {intel_id} 处理异常: {e}")
                results.append({
                    "id": intel_id,
                    "status": "error",
                    "message": f"处理异常: {str(e)}"
                })

        total_time = time.time() - start_time
        date_extracted_count = sum(1 for r in results if r.get('extracted_date'))

        print(
            f"🎉 批量AI分析完成: 成功 {success_count}/{len(intelligence_ids)} 条，日期提取 {date_extracted_count} 条，耗时 {total_time:.2f}秒")

        return {
            "status": "success",
            "message": f"批量处理完成: 成功 {success_count} 条，失败 {len(intelligence_ids) - success_count} 条，日期提取 {date_extracted_count} 条",
            "results": results,
            "success_count": success_count,
            "total_count": len(intelligence_ids),
            "date_extracted_count": date_extracted_count,
            "processing_time": round(total_time, 2),
            "average_time": round(total_time / len(intelligence_ids), 2)
        }

    except Exception as e:
        print(f"❌ 批量AI处理失败: {e}")
        return {"status": "error", "message": f"批量处理失败: {str(e)}"}


# 新增：完全隔离的单个处理函数
async def process_single_intelligence_isolated(intelligence_id: int, date_selectors: List[str] = None):
    """完全隔离的单个情报处理 - 避免并发冲突"""
    ai_db = None
    try:
        # 创建独立的session
        ai_db = await get_ai_processing_db()

        # 调用处理函数
        result = await ai_process_single_intelligence_with_date_extraction(
            intelligence_id, ai_db, date_selectors
        )

        return result

    except Exception as e:
        print(f"❌ 隔离处理失败: {e}")
        return {"status": "error", "message": str(e)}

    finally:
        # 确保session被正确关闭
        if ai_db:
            try:
                await ai_db.close()
            except Exception as close_error:
                print(f"⚠️ 关闭session时出错: {close_error}")


# 修改现有的爬虫函数，在爬虫阶段就尝试提取日期
@api_router.post("/crawl-with-date-extraction")
async def start_crawling_with_date_extraction(
        db: AsyncSession = Depends(get_db),
        topic_ids: str = Form(..., description="选择的议题ID，逗号分隔"),
        days_back: int = Form(7, description="爬取几天内的新闻"),
        max_items_per_source: int = Form(20, description="每个源最大爬取数量"),
        enable_date_extraction: bool = Form(True, description="是否启用日期提取")
):
    """智能爬取功能 - 支持在爬取阶段提取日期"""
    try:
        print("\n" + "=" * 60)
        print(f"🚀 开始智能爬取任务(含日期提取)")
        print(
            f"📋 参数: topic_ids={topic_ids}, days_back={days_back}, max_items={max_items_per_source}, date_extraction={enable_date_extraction}")
        print("=" * 60 + "\n")

        # 解析议题ID列表
        topic_id_list = [int(id.strip()) for id in topic_ids.split(',') if id.strip()]

        if not topic_id_list:
            return {"status": "error", "message": "请至少选择一个议题"}

        # 查询议题和源，获取日期选择器配置
        from sqlalchemy import select
        from models.base_models import Topic, Source, topic_source_association

        # 获取议题
        topics_result = await db.execute(
            select(Topic).where(Topic.id.in_(topic_id_list))
        )
        topics = topics_result.scalars().all()

        if not topics:
            return {"status": "error", "message": "未找到选择的议题"}

        print(f"✅ 找到 {len(topics)} 个议题")

        # 计算时间范围
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        print(f"📅 时间范围: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")

        crawl_results = []
        total_crawled = 0
        total_saved = 0
        total_date_extracted = 0  # 新增：统计日期提取成功数

        # 处理每个议题
        for topic_idx, topic in enumerate(topics, 1):
            # 提前保存topic属性，避免后续懒加载
            topic_id = topic.id
            topic_name = topic.name

            print(f"\n{'=' * 50}")
            print(f"🎯 处理议题 [{topic_idx}/{len(topics)}]: {topic_name}")

            # 获取该议题的所有源及其配置
            sources_result = await db.execute(
                select(Source)
                .join(topic_source_association)
                .where(topic_source_association.c.topic_id == topic_id)
            )
            topic_sources = sources_result.scalars().all()

            print(f"   该议题下有 {len(topic_sources)} 个信息源")
            print(f"{'=' * 50}")

            topic_results = {
                "topic_id": topic_id,
                "topic_name": topic_name,
                "sources": [],
                "total_items": 0,
                "total_saved": 0,
                "total_date_extracted": 0  # 新增
            }

            # 处理每个信息源
            for source_idx, source in enumerate(topic_sources, 1):
                # 重要：提前读取所有需要的属性
                source_domain = source.domain
                source_recipe_json = source.recipe_json

                print(f"\n   [{source_idx}/{len(topic_sources)}] 爬取信息源: {source_domain}")
                print(f"   " + "-" * 40)

                try:
                    # 解析配方，提取日期选择器
                    import json
                    if isinstance(source_recipe_json, str):
                        recipe_data = json.loads(source_recipe_json)
                    else:
                        recipe_data = source_recipe_json

                    # 获取日期选择器配置
                    date_selectors = []
                    if enable_date_extraction and recipe_data:
                        date_selector = recipe_data.get('date_selector')
                        if date_selector:
                            # 处理多个选择器（逗号分隔）
                            date_selectors = [s.strip() for s in date_selector.split(',') if s.strip()]
                            print(f"   📅 日期选择器: {date_selectors}")

                    # 创建爬虫
                    from scraper.models import ScraperRecipe
                    from scraper.scraper_main import Scraper

                    recipe = ScraperRecipe(**recipe_data)
                    scraper = Scraper(recipe)

                    # 执行爬取
                    print(f"   ⏳ 正在爬取，最多获取 {max_items_per_source} 条...")
                    start_time = datetime.now()

                    items, meta = await scraper.scrape(max_links=max_items_per_source)

                    elapsed = (datetime.now() - start_time).total_seconds()
                    print(f"   ✅ 爬取完成，耗时 {elapsed:.1f} 秒")
                    print(f"   📊 获取到 {len(items)} 条数据")

                    saved_count = 0
                    updated_count = 0
                    duplicate_count = 0
                    out_of_range_count = 0
                    date_extracted_count = 0  # 新增：当前源的日期提取统计

                    for item_idx, item in enumerate(items, 1):
                        try:
                            # 安全的时间处理（保持原有逻辑）
                            news_time = None
                            if hasattr(item, 'date') and item.date:
                                try:
                                    if isinstance(item.date, str) and item.date.strip():
                                        from dateutil import parser
                                        # 添加模糊解析支持
                                        try:
                                            news_time = parser.parse(item.date.strip())
                                        except:
                                            news_time = parser.parse(item.date.strip(), fuzzy=True)
                                    elif isinstance(item.date, datetime):
                                        news_time = item.date
                                except Exception as e:
                                    print(f"      📅 日期解析失败: {item.date}, 错误: {e}")
                                    news_time = None

                            # 如果列表页没有日期，但启用了日期提取，尝试从具体链接获取
                            if news_time is None and enable_date_extraction and date_selectors and item.url:
                                try:
                                    print(f"      🔍 尝试从链接提取日期: {item.url[:50]}...")

                                    from services.ai_service import extract_date_from_url
                                    date_result = await extract_date_from_url(item.url, date_selectors)

                                    if date_result.success:
                                        from dateutil import parser
                                        news_time = parser.parse(date_result.date)
                                        date_extracted_count += 1
                                        print(f"      ✅ 成功提取日期: {date_result.date} (方法: {date_result.method})")
                                    else:
                                        print(f"      ⚠️ 日期提取失败: {date_result.error_message}")

                                except Exception as e:
                                    print(f"      ❌ 日期提取出错: {e}")

                            # 时间范围检查（保持原有逻辑）
                            if news_time is not None:
                                if news_time < start_date - timedelta(days=7):
                                    out_of_range_count += 1
                                    continue

                            # 检查是否已存在相同标题的记录
                            existing_result = await db.execute(
                                text("SELECT id, news_time FROM intelligence WHERE title = :title LIMIT 1"),
                                {"title": item.title}
                            )
                            existing_row = existing_result.fetchone()

                            current_time = datetime.now()

                            if existing_row:
                                existing_id, existing_news_time = existing_row

                                # 如果现有记录没有日期，但新数据有日期，则更新
                                if existing_news_time is None and news_time is not None:
                                    await db.execute(
                                        text("""
                                            UPDATE intelligence 
                                            SET news_time = :news_time, 
                                                update_time = :update_time,
                                                topic = :topic
                                            WHERE id = :id
                                        """),
                                        {
                                            "id": existing_id,
                                            "news_time": news_time.isoformat(),
                                            "update_time": current_time.isoformat(),
                                            "topic": topic_name
                                        }
                                    )

                                    # 检查是否需要更新或添加来源
                                    source_check = await db.execute(
                                        text(
                                            "SELECT COUNT(*) FROM intelligence_sources WHERE intelligence_id = :id AND url = :url"),
                                        {"id": existing_id, "url": item.url}
                                    )

                                    if source_check.scalar() == 0:
                                        await db.execute(
                                            text("""
                                                INSERT INTO intelligence_sources (
                                                    intelligence_id, url, title, domain, fetch_time
                                                ) VALUES (
                                                    :intelligence_id, :url, :title, :domain, :fetch_time
                                                )
                                            """),
                                            {
                                                'intelligence_id': existing_id,
                                                'url': item.url,
                                                'title': item.title,
                                                'domain': source_domain,
                                                'fetch_time': current_time.isoformat()
                                            }
                                        )

                                    updated_count += 1
                                    print(f"      ✅ 更新记录日期: {item.title[:40]}...")
                                else:
                                    duplicate_count += 1
                                    if item_idx % 50 == 0:  # 减少重复日志
                                        print(f"      ⚠️ 已处理 {duplicate_count} 个重复记录...")
                                continue

                            # 插入新记录（保持原有逻辑）
                            news_time_str = news_time.isoformat() if news_time else None

                            insert_result = await db.execute(
                                text("""
                                    INSERT INTO intelligence (
                                        title, summary, topic, news_time, collect_time, update_time,
                                        quality_status, ai_score, score_dimensions, is_merged, merged_count
                                    ) VALUES (
                                        :title, :summary, :topic, :news_time, :collect_time, :update_time,
                                        :quality_status, :ai_score, :score_dimensions, :is_merged, :merged_count
                                    )
                                """),
                                {
                                    'title': item.title,
                                    'summary': '',
                                    'topic': topic_name,
                                    'news_time': news_time_str,
                                    'collect_time': current_time.isoformat(),
                                    'update_time': current_time.isoformat(),
                                    'quality_status': 'pending',
                                    'ai_score': 0.0,
                                    'score_dimensions': '{}',
                                    'is_merged': 0,
                                    'merged_count': 0
                                }
                            )

                            # 插入来源记录（保持原有逻辑）
                            intelligence_id = insert_result.lastrowid
                            await db.execute(
                                text("""
                                    INSERT INTO intelligence_sources (
                                        intelligence_id, url, title, domain, fetch_time
                                    ) VALUES (
                                        :intelligence_id, :url, :title, :domain, :fetch_time
                                    )
                                """),
                                {
                                    'intelligence_id': intelligence_id,
                                    'url': item.url,
                                    'title': item.title,
                                    'domain': source_domain,
                                    'fetch_time': current_time.isoformat()
                                }
                            )

                            saved_count += 1
                            if saved_count % 10 == 0:
                                print(f"      💾 已保存 {saved_count} 条新记录...")

                        except Exception as e:
                            print(f"      ❌ 保存失败 [{item_idx}]: {str(e)[:50]}")
                            continue

                    # 提交数据库
                    if saved_count > 0 or updated_count > 0:
                        await db.commit()
                        print(f"   ✅ 成功保存 {saved_count} 条新记录，更新 {updated_count} 条记录")

                    print(
                        f"   📊 统计: 新增={saved_count}, 更新={updated_count}, 重复={duplicate_count}, 超时={out_of_range_count}, 日期提取={date_extracted_count}")

                    # 构建结果（使用保存的变量）
                    source_result = {
                        "domain": source_domain,
                        "status": "success",
                        "crawled": len(items),
                        "saved": saved_count,
                        "duplicate": duplicate_count,
                        "out_of_range": out_of_range_count,
                        "date_extracted": date_extracted_count,  # 新增
                        "meta": meta
                    }

                    topic_results["sources"].append(source_result)
                    topic_results["total_items"] += len(items)
                    topic_results["total_saved"] += saved_count
                    topic_results["total_date_extracted"] += date_extracted_count  # 新增

                    total_crawled += len(items)
                    total_saved += saved_count
                    total_date_extracted += date_extracted_count  # 新增

                except Exception as e:
                    print(f"   ❌ 爬取失败: {e}")

                    # 错误处理（使用保存的变量）
                    topic_results["sources"].append({
                        "domain": source_domain,
                        "status": "error",
                        "message": str(e)[:200]
                    })

            crawl_results.append(topic_results)
            print(
                f"\n✅ 议题 '{topic_name}' 处理完成: 爬取={topic_results['total_items']}, 保存={topic_results['total_saved']}, 日期提取={topic_results['total_date_extracted']}")

        # 完成总结
        print("\n" + "=" * 60)
        print(f"🎉 爬取任务完成!")
        print(f"📊 总计: 爬取={total_crawled} 条, 保存={total_saved} 条, 日期提取={total_date_extracted} 条")
        print("=" * 60 + "\n")

        return {
            "status": "success",
            "message": f"成功爬取 {total_crawled} 条，保存 {total_saved} 条情报，日期提取 {total_date_extracted} 条",
            "total_crawled": total_crawled,
            "total_saved": total_saved,
            "total_date_extracted": total_date_extracted,  # 新增
            "time_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days_back
            },
            "results": crawl_results
        }

    except Exception as e:
        print(f"\n❌ 爬取任务失败: {e}")
        import traceback
        traceback.print_exc()
        await db.rollback()  # 添加回滚
        return {"status": "error", "message": f"爬取失败: {str(e)}"}


@api_router.get("/topics")
async def get_all_topics(db: AsyncSession = Depends(get_db)):
    """获取所有可用的议题列表"""
    try:
        result = await db.execute(
            select(Topic).options(selectinload(Topic.sources)).order_by(Topic.name)
        )
        topics = result.scalars().all()

        return {
            "status": "success",
            "topics": [
                {
                    "id": topic.id,
                    "name": topic.name,
                    "description": topic.description or "",
                    "source_count": len(topic.sources) if topic.sources else 0,
                    "domains": [source.domain for source in topic.sources] if topic.sources else []
                }
                for topic in topics
            ]
        }
    except Exception as e:
        print(f"获取议题失败: {e}")
        return {"status": "error", "message": str(e)}


@api_router.post("/crawl")
async def start_crawling(
        db: AsyncSession = Depends(get_db),
        topic_ids: str = Form(..., description="选择的议题ID，逗号分隔"),
        days_back: int = Form(7, description="爬取几天内的新闻"),
        max_items_per_source: int = Form(20, description="每个源最大爬取数量")
):
    """智能爬取功能 - 完整修复版"""
    try:
        print("\n" + "=" * 60)
        print(f"🚀 开始智能爬取任务")
        print(f"📝 参数: topic_ids={topic_ids}, days_back={days_back}, max_items={max_items_per_source}")
        print("=" * 60 + "\n")

        # 解析议题ID列表
        topic_id_list = [int(id.strip()) for id in topic_ids.split(',') if id.strip()]

        if not topic_id_list:
            return {"status": "error", "message": "请至少选择一个议题"}

        # 查询议题和源
        from sqlalchemy import select
        from models.base_models import Topic, Source, topic_source_association

        # 获取议题
        topics_result = await db.execute(
            select(Topic).where(Topic.id.in_(topic_id_list))
        )
        topics = topics_result.scalars().all()

        if not topics:
            return {"status": "error", "message": "未找到选择的议题"}

        print(f"✅ 找到 {len(topics)} 个议题")

        # 计算时间范围
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        print(f"📅 时间范围: {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")

        crawl_results = []
        total_crawled = 0
        total_saved = 0

        # 处理每个议题
        for topic_idx, topic in enumerate(topics, 1):
            # 提前保存topic属性，避免后续懒加载
            topic_id = topic.id
            topic_name = topic.name

            print(f"\n{'=' * 50}")
            print(f"🎯 处理议题 [{topic_idx}/{len(topics)}]: {topic_name}")

            # 获取该议题的所有源
            sources_result = await db.execute(
                select(Source)
                .join(topic_source_association)
                .where(topic_source_association.c.topic_id == topic_id)
            )
            topic_sources = sources_result.scalars().all()

            print(f"   该议题下有 {len(topic_sources)} 个信息源")
            print(f"{'=' * 50}")

            topic_results = {
                "topic_id": topic_id,
                "topic_name": topic_name,
                "sources": [],
                "total_items": 0,
                "total_saved": 0
            }

            # 处理每个信息源
            for source_idx, source in enumerate(topic_sources, 1):
                # 重要：提前读取所有需要的属性
                source_domain = source.domain
                source_recipe_json = source.recipe_json

                print(f"\n   [{source_idx}/{len(topic_sources)}] 爬取信息源: {source_domain}")
                print(f"   " + "-" * 40)

                try:
                    # 解析配方
                    import json
                    if isinstance(source_recipe_json, str):
                        recipe_data = json.loads(source_recipe_json)
                    else:
                        recipe_data = source_recipe_json

                    # 创建爬虫
                    from scraper.models import ScraperRecipe
                    from scraper.scraper_main import Scraper

                    recipe = ScraperRecipe(**recipe_data)
                    scraper = Scraper(recipe)

                    # 执行爬取
                    print(f"   ⏳ 正在爬取，最多获取 {max_items_per_source} 条...")
                    start_time = datetime.now()

                    items, meta = await scraper.scrape(max_links=max_items_per_source)

                    elapsed = (datetime.now() - start_time).total_seconds()
                    print(f"   ✅ 爬取完成，耗时 {elapsed:.1f} 秒")
                    print(f"   📊 获取到 {len(items)} 条数据")

                    saved_count = 0
                    updated_count = 0
                    duplicate_count = 0
                    out_of_range_count = 0

                    for item_idx, item in enumerate(items, 1):
                        try:
                            # 安全的时间处理（保持原有逻辑）
                            news_time = None
                            if hasattr(item, 'date') and item.date:
                                try:
                                    if isinstance(item.date, str) and item.date.strip():
                                        from dateutil import parser
                                        # 添加模糊解析支持
                                        try:
                                            news_time = parser.parse(item.date.strip())
                                        except:
                                            news_time = parser.parse(item.date.strip(), fuzzy=True)
                                    elif isinstance(item.date, datetime):
                                        news_time = item.date
                                except Exception as e:
                                    print(f"      📅 日期解析失败: {item.date}, 错误: {e}")
                                    news_time = None

                            # 时间范围检查（保持原有逻辑）
                            if news_time is not None:
                                if news_time < start_date - timedelta(days=7):
                                    out_of_range_count += 1
                                    continue

                            # 检查是否已存在相同标题的记录
                            existing_result = await db.execute(
                                text("SELECT id, news_time FROM intelligence WHERE title = :title LIMIT 1"),
                                {"title": item.title}
                            )
                            existing_row = existing_result.fetchone()

                            current_time = datetime.now()

                            if existing_row:
                                existing_id, existing_news_time = existing_row

                                # 如果现有记录没有日期，但新数据有日期，则更新
                                if existing_news_time is None and news_time is not None:
                                    await db.execute(
                                        text("""
                                            UPDATE intelligence 
                                            SET news_time = :news_time, 
                                                update_time = :update_time,
                                                topic = :topic
                                            WHERE id = :id
                                        """),
                                        {
                                            "id": existing_id,
                                            "news_time": news_time.isoformat(),
                                            "update_time": current_time.isoformat(),
                                            "topic": topic_name
                                        }
                                    )

                                    # 检查是否需要更新或添加来源
                                    source_check = await db.execute(
                                        text(
                                            "SELECT COUNT(*) FROM intelligence_sources WHERE intelligence_id = :id AND url = :url"),
                                        {"id": existing_id, "url": item.url}
                                    )

                                    if source_check.scalar() == 0:
                                        await db.execute(
                                            text("""
                                                INSERT INTO intelligence_sources (
                                                    intelligence_id, url, title, domain, fetch_time
                                                ) VALUES (
                                                    :intelligence_id, :url, :title, :domain, :fetch_time
                                                )
                                            """),
                                            {
                                                'intelligence_id': existing_id,
                                                'url': item.url,
                                                'title': item.title,
                                                'domain': source_domain,
                                                'fetch_time': current_time.isoformat()
                                            }
                                        )

                                    updated_count += 1
                                    print(f"      ✅ 更新记录日期: {item.title[:40]}...")
                                else:
                                    duplicate_count += 1
                                    if item_idx % 50 == 0:  # 减少重复日志
                                        print(f"      ⚠️ 已处理 {duplicate_count} 个重复记录...")
                                continue

                            # 插入新记录（保持原有逻辑）
                            news_time_str = news_time.isoformat() if news_time else None

                            insert_result = await db.execute(
                                text("""
                                    INSERT INTO intelligence (
                                        title, summary, topic, news_time, collect_time, update_time,
                                        quality_status, ai_score, score_dimensions, is_merged, merged_count
                                    ) VALUES (
                                        :title, :summary, :topic, :news_time, :collect_time, :update_time,
                                        :quality_status, :ai_score, :score_dimensions, :is_merged, :merged_count
                                    )
                                """),
                                {
                                    'title': item.title,
                                    'summary': '',
                                    'topic': topic_name,
                                    'news_time': news_time_str,
                                    'collect_time': current_time.isoformat(),
                                    'update_time': current_time.isoformat(),
                                    'quality_status': 'pending',
                                    'ai_score': 0.0,
                                    'score_dimensions': '{}',
                                    'is_merged': 0,
                                    'merged_count': 0
                                }
                            )

                            # 插入来源记录（保持原有逻辑）
                            intelligence_id = insert_result.lastrowid
                            await db.execute(
                                text("""
                                    INSERT INTO intelligence_sources (
                                        intelligence_id, url, title, domain, fetch_time
                                    ) VALUES (
                                        :intelligence_id, :url, :title, :domain, :fetch_time
                                    )
                                """),
                                {
                                    'intelligence_id': intelligence_id,
                                    'url': item.url,
                                    'title': item.title,
                                    'domain': source_domain,
                                    'fetch_time': current_time.isoformat()
                                }
                            )

                            saved_count += 1
                            if saved_count % 10 == 0:
                                print(f"      💾 已保存 {saved_count} 条新记录...")

                        except Exception as e:
                            print(f"      ❌ 保存失败 [{item_idx}]: {str(e)[:50]}")
                            continue

                    # 提交数据库
                    if saved_count > 0 or updated_count > 0:
                        await db.commit()
                        print(f"   ✅ 成功保存 {saved_count} 条新记录，更新 {updated_count} 条记录")

                    print(
                        f"   📊 统计: 新增={saved_count}, 更新={updated_count}, 重复={duplicate_count}, 超时={out_of_range_count}")

                    # 构建结果（使用保存的变量）
                    source_result = {
                        "domain": source_domain,
                        "status": "success",
                        "crawled": len(items),
                        "saved": saved_count,
                        "duplicate": duplicate_count,
                        "out_of_range": out_of_range_count,
                        "meta": meta
                    }

                    topic_results["sources"].append(source_result)
                    topic_results["total_items"] += len(items)
                    topic_results["total_saved"] += saved_count
                    total_crawled += len(items)
                    total_saved += saved_count

                except Exception as e:
                    print(f"   ❌ 爬取失败: {e}")

                    # 错误处理（使用保存的变量）
                    topic_results["sources"].append({
                        "domain": source_domain,
                        "status": "error",
                        "message": str(e)[:200]
                    })

            crawl_results.append(topic_results)
            print(
                f"\n✅ 议题 '{topic_name}' 处理完成: 爬取={topic_results['total_items']}, 保存={topic_results['total_saved']}")

        # 完成总结
        print("\n" + "=" * 60)
        print(f"🎉 爬取任务完成!")
        print(f"📊 总计: 爬取={total_crawled} 条, 保存={total_saved} 条")
        print("=" * 60 + "\n")

        return {
            "status": "success",
            "message": f"成功爬取 {total_crawled} 条，保存 {total_saved} 条情报",
            "total_crawled": total_crawled,
            "total_saved": total_saved,
            "time_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days_back
            },
            "results": crawl_results
        }

    except Exception as e:
        print(f"\n❌ 爬取任务失败: {e}")
        import traceback
        traceback.print_exc()
        await db.rollback()  # 添加回滚
        return {"status": "error", "message": f"爬取失败: {str(e)}"}


@api_router.get("/list", response_model=Dict[str, Any])
async def get_intelligence_list(
        db: AsyncSession = Depends(get_db),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=10, le=100),
        title: Optional[str] = Query(None),
        topic: Optional[str] = Query(None),
        quality: Optional[str] = Query(None),
        min_score: Optional[float] = Query(None),
        max_score: Optional[float] = Query(None),
        # 修改：添加新闻时间筛选参数
        news_start_date: Optional[str] = Query(None, description="按新闻时间筛选的开始日期"),
        news_end_date: Optional[str] = Query(None, description="按新闻时间筛选的结束日期"),
        # 保留原有的收集时间筛选参数（向后兼容）
        start_date: Optional[str] = Query(None, description="按收集时间筛选的开始日期"),
        end_date: Optional[str] = Query(None, description="按收集时间筛选的结束日期"),
        sort_by: str = Query("news_time", description="排序字段，默认按新闻时间"),  # 修改默认排序
        order: str = Query("desc")
):
    """获取情报列表 - 支持按新闻时间筛选和排序"""
    try:
        print(f"📋 查询参数: page={page}, page_size={page_size}")
        print(f"📅 新闻时间筛选: {news_start_date} 到 {news_end_date}")

        # 重要：将所有参数转换为Python原生类型，避免Query对象问题
        title_str = str(title) if title is not None else None
        topic_str = str(topic) if topic is not None else None
        quality_str = str(quality) if quality is not None else None
        news_start_date_str = str(news_start_date) if news_start_date is not None else None
        news_end_date_str = str(news_end_date) if news_end_date is not None else None
        start_date_str = str(start_date) if start_date is not None else None
        end_date_str = str(end_date) if end_date is not None else None

        print(f"📋 转换后参数: title={title_str}, topic={topic_str}, quality={quality_str}")

        # 构建SQL查询条件
        where_conditions = []
        params = {}

        # 字符串条件 - 严格检查
        if title_str and title_str.strip() and title_str not in ["None", "null", ""]:
            where_conditions.append("title LIKE :title")
            params['title'] = f"%{title_str.strip()}%"

        if topic_str and topic_str.strip() and topic_str not in ["None", "null", ""]:
            where_conditions.append("topic LIKE :topic")
            params['topic'] = f"%{topic_str.strip()}%"

        if quality_str and quality_str.strip() and quality_str not in ["None", "null", ""]:
            where_conditions.append("quality_status = :quality")
            params['quality'] = quality_str.strip()

        # 数字条件
        if min_score is not None and str(min_score) not in ["None", "null", ""]:
            try:
                score_val = float(min_score)
                where_conditions.append("ai_score >= :min_score")
                params['min_score'] = score_val
            except (ValueError, TypeError):
                pass

        if max_score is not None and str(max_score) not in ["None", "null", ""]:
            try:
                score_val = float(max_score)
                where_conditions.append("ai_score <= :max_score")
                params['max_score'] = score_val
            except (ValueError, TypeError):
                pass

        # 新闻时间筛选（优先级高）
        if news_start_date_str and news_start_date_str not in ["None", "null", ""]:
            try:
                from dateutil import parser
                start_dt = parser.parse(news_start_date_str)
                where_conditions.append("news_time >= :news_start_date")
                params['news_start_date'] = start_dt
                print(f"📅 添加新闻开始时间筛选: {start_dt}")
            except Exception as e:
                print(f"⚠️ 新闻开始时间解析失败: {e}")

        if news_end_date_str and news_end_date_str not in ["None", "null", ""]:
            try:
                from dateutil import parser
                end_dt = parser.parse(news_end_date_str)
                where_conditions.append("news_time <= :news_end_date")
                params['news_end_date'] = end_dt
                print(f"📅 添加新闻结束时间筛选: {end_dt}")
            except Exception as e:
                print(f"⚠️ 新闻结束时间解析失败: {e}")

        # 收集时间筛选（向后兼容，仅在没有新闻时间筛选时使用）
        elif start_date_str and start_date_str not in ["None", "null", ""]:
            try:
                from dateutil import parser
                start_dt = parser.parse(start_date_str)
                where_conditions.append("collect_time >= :start_date")
                params['start_date'] = start_dt
                print(f"📅 添加收集开始时间筛选: {start_dt}")
            except Exception as e:
                print(f"⚠️ 收集开始时间解析失败: {e}")

        elif end_date_str and end_date_str not in ["None", "null", ""]:
            try:
                from dateutil import parser
                end_dt = parser.parse(end_date_str)
                where_conditions.append("collect_time <= :end_date")
                params['end_date'] = end_dt
                print(f"📅 添加收集结束时间筛选: {end_dt}")
            except Exception as e:
                print(f"⚠️ 收集结束时间解析失败: {e}")

        # 构建WHERE子句
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)

        print(f"📋 WHERE子句: {where_clause}")
        print(f"📋 SQL参数: {params}")

        # 构建排序 - 支持按新闻时间排序
        valid_sort_fields = ["id", "title", "topic", "news_time", "collect_time", "ai_score", "quality_status"]
        if sort_by not in valid_sort_fields:
            sort_by = "news_time"  # 默认按新闻时间排序

        # 特殊处理新闻时间排序：NULL值排在最后
        if sort_by == "news_time":
            order_clause = f"ORDER BY {sort_by} IS NULL, {sort_by} {'DESC' if order.lower() == 'desc' else 'ASC'}"
        else:
            order_clause = f"ORDER BY {sort_by} {'DESC' if order.lower() == 'desc' else 'ASC'}"

        # 查询总数
        from sqlalchemy import text
        count_sql = f"SELECT COUNT(*) FROM intelligence {where_clause}"
        count_result = await db.execute(text(count_sql), params)
        total = count_result.scalar() or 0

        print(f"📊 查询总数结果: {total}")

        # 分页查询
        offset = (page - 1) * page_size
        data_sql = f"""
            SELECT 
                id, title, summary, topic, news_time, collect_time, update_time,
                ai_score, score_dimensions, quality_status, is_merged, merged_count
            FROM intelligence 
            {where_clause} 
            {order_clause} 
            LIMIT :limit OFFSET :offset
        """

        params.update({
            'limit': page_size,
            'offset': offset
        })

        # 执行查询
        result = await db.execute(text(data_sql), params)
        rows = result.fetchall()

        print(f"📋 查询到 {len(rows)} 条记录")

        # 安全的时间格式化函数
        def safe_format_datetime(dt_value):
            """安全地格式化时间值"""
            if dt_value is None:
                return None

            # 如果已经是字符串，直接返回
            if isinstance(dt_value, str):
                return dt_value

            # 如果是datetime对象，转换为ISO格式
            try:
                return dt_value.isoformat()
            except Exception as e:
                print(f"⚠️ 时间格式化失败: {dt_value}, 错误: {e}")
                return str(dt_value)

        # 构建响应数据
        items = []
        for row in rows:
            try:
                # 查询来源
                sources_result = await db.execute(
                    text("SELECT url, title FROM intelligence_sources WHERE intelligence_id = :id"),
                    {"id": row.id}
                )
                sources = []
                for s in sources_result.fetchall():
                    sources.append({"url": s.url, "title": s.title or ""})

                # 解析JSON字段
                score_dimensions = {}
                if row.score_dimensions:
                    try:
                        import json
                        score_dimensions = json.loads(row.score_dimensions)
                    except Exception as json_error:
                        print(f"⚠️ JSON解析失败 {row.id}: {json_error}")

                item = {
                    "id": row.id,
                    "title": row.title,
                    "summary": row.summary or "",
                    "news_time": safe_format_datetime(row.news_time),  # 新闻时间
                    "collect_time": safe_format_datetime(row.collect_time),  # 收集时间
                    "topic": row.topic or "",
                    "category": "前沿资讯",
                    "ai_score": float(row.ai_score or 0),
                    "dimensions": score_dimensions,
                    "quality_status": row.quality_status or "pending",
                    "competitors": [],
                    "sources": sources,
                    "is_merged": bool(row.is_merged),
                    "merged_count": int(row.merged_count or 0)
                }
                items.append(item)

            except Exception as item_error:
                print(f"⚠️ 处理记录 {row.id} 时出错: {item_error}")
                import traceback
                traceback.print_exc()
                continue

        print(f"✅ 成功构建 {len(items)} 条记录")

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0
        }

    except Exception as e:
        print(f"❌ 查询错误: {e}")
        import traceback
        traceback.print_exc()
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": page_size,
            "total_pages": 0,
            "error": str(e)
        }


@pages_router.get("/partial/table", response_class=HTMLResponse)
async def get_intelligence_table(
        request: Request,
        db: AsyncSession = Depends(get_db),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=10, le=100),
        title: Optional[str] = Query(None),
        topic: Optional[str] = Query(None),
        quality: Optional[str] = Query(None),
        min_score: Optional[float] = Query(None),
        max_score: Optional[float] = Query(None),
        # 添加新闻时间筛选参数
        news_start_date: Optional[str] = Query(None),
        news_end_date: Optional[str] = Query(None),
        sort_by: str = Query("news_time"),  # 默认按新闻时间排序
        order: str = Query("desc")
):
    """获取情报表格局部视图 - 支持新闻时间筛选"""
    try:
        print(f"📊 表格请求: page={page}, size={page_size}")
        print(f"📅 新闻时间筛选: {news_start_date} 到 {news_end_date}")

        # 显式传递所有参数，避免Query对象问题
        data = await get_intelligence_list(
            db=db,
            page=page,
            page_size=page_size,
            title=title,
            topic=topic,
            quality=quality,
            min_score=min_score,
            max_score=max_score,
            news_start_date=news_start_date,  # 新增
            news_end_date=news_end_date,  # 新增
            start_date=None,  # 显式传递None
            end_date=None,  # 显式传递None
            sort_by=sort_by,
            order=order
        )

        print(f"📋 获取数据: total={data.get('total', 0)}, items={len(data.get('items', []))}")

        # 生成HTML
        html_content = generate_table_html(data)
        return HTMLResponse(content=html_content)

    except Exception as e:
        print(f"❌ 表格生成错误: {e}")
        import traceback
        traceback.print_exc()

        error_html = f"""
        <div class="intelligence-table-container">
            <div class="alert alert-danger m-4">
                <h5><i class="bi bi-exclamation-triangle"></i> 加载失败</h5>
                <p>错误: {str(e)}</p>
                <button class="btn btn-primary mt-2" onclick="loadTableData()">
                    <i class="bi bi-arrow-clockwise"></i> 重试
                </button>
            </div>
        </div>
        """
        return HTMLResponse(content=error_html, status_code=200)


# 修复并发问题的批量AI处理
@api_router.post("/batch-ai-process")
async def batch_ai_process(
        db: AsyncSession = Depends(get_db),
        request_data: dict = Body(...)
):
    """批量AI处理 - 修复并发问题版本"""
    try:
        intelligence_ids = request_data.get("intelligence_ids", [])
        if not intelligence_ids:
            return {"status": "error", "message": "请提供要处理的情报ID列表"}

        print(f"🚀 开始批量AI分析: {len(intelligence_ids)} 条情报")

        # 关键修复：使用串行处理而不是并发，避免session冲突
        results = []
        success_count = 0
        start_time = time.time()

        for idx, intel_id in enumerate(intelligence_ids, 1):
            try:
                print(f"🤖 处理情报 {intel_id} ({idx}/{len(intelligence_ids)})...")

                # 为每个AI处理创建独立的session
                ai_db = await get_ai_processing_db()
                try:
                    result = await ai_process_single_intelligence(intel_id, ai_db)
                    results.append({"id": intel_id, **result})

                    if result["status"] == "success":
                        success_count += 1
                        print(f"✅ 情报 {intel_id} 分析成功，评分: {result.get('ai_score', 'N/A')}")
                    else:
                        print(f"⚠️ 情报 {intel_id} 分析失败: {result['message']}")

                finally:
                    await ai_db.close()  # 确保session关闭

                # 短暂延迟，避免过快请求导致问题
                if idx < len(intelligence_ids):
                    await asyncio.sleep(0.2)

            except Exception as e:
                print(f"❌ 情报 {intel_id} 处理异常: {e}")
                results.append({
                    "id": intel_id,
                    "status": "error",
                    "message": f"处理异常: {str(e)}"
                })

        total_time = time.time() - start_time
        print(f"🎉 批量AI分析完成: 成功 {success_count}/{len(intelligence_ids)} 条，耗时 {total_time:.2f}秒")

        return {
            "status": "success",
            "message": f"批量处理完成: 成功 {success_count} 条，失败 {len(intelligence_ids) - success_count} 条",
            "results": results,
            "success_count": success_count,
            "total_count": len(intelligence_ids),
            "processing_time": round(total_time, 2),
            "average_time": round(total_time / len(intelligence_ids), 2)
        }

    except Exception as e:
        print(f"❌ 批量AI处理失败: {e}")
        return {"status": "error", "message": f"批量处理失败: {str(e)}"}


async def ai_process_single_intelligence(
        intelligence_id: int,
        db: AsyncSession
):
    """单个情报AI处理 - 使用独立session"""
    try:
        print(f"🤖 开始AI分析情报 ID: {intelligence_id}")

        # 使用原生SQL获取情报和来源，提升性能
        from sqlalchemy import text
        result = await db.execute(
            text("""
                SELECT i.id, i.title, i.summary, i.topic, i.news_time, i.content,
                       s.url, s.title as source_title, s.domain
                FROM intelligence i
                LEFT JOIN intelligence_sources s ON i.id = s.intelligence_id
                WHERE i.id = :id
                LIMIT 1
            """),
            {"id": intelligence_id}
        )

        row = result.fetchone()
        if not row:
            return {"status": "error", "message": "情报不存在"}

        # 构建NewsArticle对象，使用改进的时间处理
        from services.ai_service import analyze_article_with_deepseek, NewsArticle

        # 安全的时间处理
        def safe_format_datetime(dt_value):
            if dt_value is None:
                return ""
            try:
                if isinstance(dt_value, str):
                    return dt_value
                elif hasattr(dt_value, 'isoformat'):
                    return dt_value.isoformat()
                else:
                    return str(dt_value)
            except Exception:
                return str(dt_value) if dt_value else ""

        article = NewsArticle(
            source=row.domain or "unknown",
            title=row.title,
            url=row.url or "",
            publish_date=safe_format_datetime(row.news_time),
            content=row.content or row.summary or row.title,  # 优先使用content
            content_hash=""
        )

        print(f"📄 准备分析文章: {article.title[:50]}...")

        # 调用AI分析，增加超时控制
        analysis = await asyncio.wait_for(
            analyze_article_with_deepseek(article),
            timeout=30  # 30秒超时
        )

        print(f"🎯 AI分析完成")

        # 计算综合评分 - 使用新的评分维度
        scores = analysis.get("评分详情", {})
        weights = {
            "战略相关性": 0.30,
            "行业影响力": 0.20,
            "时效性紧迫性": 0.20,
            "业务机会风险强度": 0.15,
            "可操作性": 0.15
        }

        total_score = 0
        for dimension, weight in weights.items():
            score_data = scores.get(dimension, {})
            if isinstance(score_data, dict) and "分数" in score_data:
                score_val = score_data["分数"]
                total_score += score_val * weight
                print(f"维度 {dimension}: 分数={score_val}, 权重={weight}, 贡献={score_val * weight}")

        print(f"计算得出的综合评分: {total_score}")

        # 更新数据库
        import json
        await db.execute(
            text("""
                UPDATE intelligence 
                SET topic = :topic,
                    summary = :summary,
                    ai_score = :ai_score,
                    score_dimensions = :score_dimensions,
                    update_time = :update_time
                WHERE id = :id
            """),
            {
                "id": intelligence_id,
                "topic": analysis.get("议题", "未分类"),
                "summary": analysis.get("摘要", row.title),
                "ai_score": round(total_score, 1),
                "score_dimensions": json.dumps(scores, ensure_ascii=False),
                "update_time": datetime.now().isoformat()
            }
        )

        await db.commit()

        print(f"✅ AI分析完成，评分: {round(total_score, 1)}")

        return {
            "status": "success",
            "ai_score": round(total_score, 1),
            "dimensions": scores,
            "topic": analysis.get("议题"),
            "summary": analysis.get("摘要"),
            "category": analysis.get("类别")
        }

    except asyncio.TimeoutError:
        print(f"⏰ AI分析超时: 情报 {intelligence_id}")
        return {"status": "error", "message": "AI分析超时"}
    except Exception as e:
        print(f"❌ AI分析失败: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"AI分析失败: {str(e)}"}


@api_router.post("/{intelligence_id}/ai-process")
async def ai_process_intelligence(
        intelligence_id: int,
        db: AsyncSession = Depends(get_db)
):
    """单条情报AI处理"""
    return await ai_process_single_intelligence(intelligence_id, db)


@api_router.delete("/{intelligence_id}")
async def delete_intelligence(
        intelligence_id: int,
        db: AsyncSession = Depends(get_db)
):
    """删除情报"""
    try:
        result = await db.execute(
            select(Intelligence).where(Intelligence.id == intelligence_id)
        )
        intelligence = result.scalar_one_or_none()

        if not intelligence:
            raise HTTPException(status_code=404, detail="情报不存在")

        await db.delete(intelligence)
        await db.commit()

        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


@api_router.get("/{intelligence_id}")
async def get_intelligence_detail(
        intelligence_id: int,
        db: AsyncSession = Depends(get_db)
):
    """获取情报详情（用于编辑）"""
    try:
        result = await db.execute(
            select(Intelligence).options(
                selectinload(Intelligence.sources),
                selectinload(Intelligence.competitors)
            ).where(Intelligence.id == intelligence_id)
        )
        intelligence = result.scalar_one_or_none()

        if not intelligence:
            raise HTTPException(status_code=404, detail="情报不存在")

        return {
            "status": "success",
            "data": {
                "id": intelligence.id,
                "title": intelligence.title,
                "summary": intelligence.summary or "",
                "topic": intelligence.topic or "",
                "news_time": intelligence.news_time.isoformat() if intelligence.news_time else None,
                "quality_status": intelligence.quality_status,
                "ai_score": intelligence.ai_score,
                "sources": [{"url": s.url, "title": s.title} for s in intelligence.sources]
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@api_router.put("/{intelligence_id}")
async def update_intelligence(
        intelligence_id: int,
        update_data: IntelligenceUpdate,
        db: AsyncSession = Depends(get_db)
):
    """更新情报信息"""
    try:
        result = await db.execute(
            select(Intelligence).where(Intelligence.id == intelligence_id)
        )
        intelligence = result.scalar_one_or_none()

        if not intelligence:
            raise HTTPException(status_code=404, detail="情报不存在")

        # 更新字段
        for field, value in update_data.dict(exclude_unset=True).items():
            if hasattr(intelligence, field):
                setattr(intelligence, field, value)

        intelligence.update_time = datetime.now()

        await db.commit()
        return {"status": "success", "message": "更新成功"}

    except Exception as e:
        await db.rollback()
        return {"status": "error", "message": str(e)}


@api_router.patch("/{intelligence_id}/quality")
async def update_quality_status(
        intelligence_id: int,
        status: str = Body(..., embed=True),
        db: AsyncSession = Depends(get_db)
):
    """更新质量状态"""
    try:
        # 验证状态值
        valid_statuses = ["pending", "approved", "rejected"]
        if status not in valid_statuses:
            return {"status": "error", "message": "无效的状态值"}

        result = await db.execute(
            select(Intelligence).where(Intelligence.id == intelligence_id)
        )
        intelligence = result.scalar_one_or_none()

        if not intelligence:
            raise HTTPException(status_code=404, detail="情报不存在")

        intelligence.quality_status = status
        intelligence.reviewed_at = datetime.now()

        await db.commit()
        return {"status": "success", "quality_status": status}

    except Exception as e:
        return {"status": "error", "message": str(e)}


def generate_table_html(data):
    """生成表格HTML - 完全修复版"""
    items = data.get("items", [])
    total = data.get("total", 0)
    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    total_pages = data.get("total_pages", 0)

    if not items:
        return """
        <div class="intelligence-table-container">
            <div class="empty-state text-center py-5">
                <i class="bi bi-inbox" style="font-size: 48px; color: #dee2e6;"></i>
                <p class="mt-3 text-muted">暂无情报数据</p>
                <small>点击"智能爬取"开始收集情报</small>
                <div class="mt-3">
                    <button class="btn btn-primary" onclick="openCrawlModal()">
                        <i class="bi bi-cloud-download"></i> 开始爬取
                    </button>
                </div>
            </div>
        </div>
        """

    # 生成表格行
    rows = []
    for item in items:
        # 获取来源链接
        source_url = "#"
        if item.get('sources') and len(item['sources']) > 0:
            source_url = item['sources'][0].get('url', '#')

        # 修复：优先显示新闻时间
        display_time = "-"
        if item.get('news_time'):
            try:
                from datetime import datetime
                time_str = item['news_time']
                if isinstance(time_str, str):
                    if 'T' in time_str:
                        dt = datetime.fromisoformat(time_str.replace('Z', ''))
                        display_time = dt.strftime('%Y-%m-%d')
                    else:
                        display_time = time_str[:10]
                else:
                    display_time = time_str.strftime('%Y-%m-%d')
            except Exception as e:
                print(f"新闻时间格式化错误: {e}")
                # 备用：使用收集时间
                if item.get('collect_time'):
                    try:
                        collect_str = item['collect_time']
                        if isinstance(collect_str, str) and 'T' in collect_str:
                            dt = datetime.fromisoformat(collect_str.replace('Z', ''))
                            display_time = dt.strftime('%Y-%m-%d')
                        else:
                            display_time = str(collect_str)[:10] if collect_str else "-"
                    except:
                        display_time = "-"

        # AI评分详情
        score = float(item.get('ai_score', 0))
        score_class = 'success' if score >= 7 else 'warning' if score >= 5 else 'secondary'
        score_icon = 'star-fill' if score >= 7 else 'star-half' if score >= 5 else 'star'

        # 构建AI评分tooltip
        dimensions = item.get('dimensions', {})
        tooltip_content = "暂无AI评分详情"

        if dimensions:
            tooltip_parts = []
            business_impact = dimensions.get('业务影响', dimensions.get('business_impact', {}))
            if isinstance(business_impact, dict):
                score_val = business_impact.get('分数', 0)
                reason = business_impact.get('理由', '')[:100] + (
                    '...' if len(business_impact.get('理由', '')) > 100 else '')
                tooltip_parts.append(f"业务影响: {score_val}/10 - {reason}")

            reliability = dimensions.get('可靠性', dimensions.get('reliability', {}))
            if isinstance(reliability, dict):
                score_val = reliability.get('分数', 0)
                reason = reliability.get('理由', '')[:100] + ('...' if len(reliability.get('理由', '')) > 100 else '')
                tooltip_parts.append(f"可靠性: {score_val}/10 - {reason}")

            timeliness = dimensions.get('时效性', dimensions.get('timeliness', {}))
            if isinstance(timeliness, dict):
                score_val = timeliness.get('分数', 0)
                reason = timeliness.get('理由', '')[:100] + ('...' if len(timeliness.get('理由', '')) > 100 else '')
                tooltip_parts.append(f"时效性: {score_val}/10 - {reason}")

            if tooltip_parts:
                tooltip_content = "\\n".join(tooltip_parts)

        tooltip_content = html.escape(tooltip_content).replace('"', '&quot;').replace("'", '&#39;')

        # 状态配置
        status = item.get('quality_status', 'pending')
        all_status_options = f"""
            <option value="pending" {'selected' if status == 'pending' else ''}>⏳ 待审核</option>
            <option value="approved" {'selected' if status == 'approved' else ''}>✅ 已通过</option>
            <option value="rejected" {'selected' if status == 'rejected' else ''}>❌ 已拒绝</option>
        """

        # 修复：竞争对手自动识别和手动标记
        competitors = item.get('competitors', [])
        competitor_badge = ""
        title_lower = item['title'].lower()
        summary_lower = (item.get('summary', '') or '').lower()

        # 自动识别竞争对手关键词
        competitor_keywords = [
            '竞争', '对手', '同行', '竞品', 'competitor', 'rival',
            '挑战', 'challenge', '超越', '领先', '市场份额'
        ]
        is_competitor = any(keyword in title_lower or keyword in summary_lower for keyword in competitor_keywords)

        if is_competitor or len(competitors) > 0:
            competitor_badge = f'''
                <span class="badge bg-danger ms-1" onclick="toggleCompetitor({item['id']})" 
                      style="cursor: pointer;" title="点击取消竞争对手标记">
                    🔴 竞争对手
                </span>
            '''
        else:
            # 添加标记为竞争对手的按钮
            competitor_badge = f'''
                <span class="badge bg-outline-secondary ms-1" onclick="toggleCompetitor({item['id']})" 
                      style="cursor: pointer; border: 1px dashed #ccc; color: #666;" 
                      title="点击标记为竞争对手">
                    ➕ 标记竞争对手
                </span>
            '''

        # 议题和类别
        topic = html.escape(item.get('topic', '未分类'))
        category = html.escape(item.get('category', '前沿资讯'))

        # 摘要处理 - 限制长度避免界面混乱
        summary = item.get('summary', '')
        if summary and summary.strip():
            display_summary = summary[:150] + ('...' if len(summary) > 150 else '')
        else:
            display_summary = item['title'][:80] + ('...' if len(item['title']) > 80 else '')

        display_summary = html.escape(display_summary)

        # 构建表格行 - 修复列结构
        row = f"""
        <tr data-id="{item['id']}" class="intelligence-row">
            <td class="text-center checkbox-col">
                <input type="checkbox" class="form-check-input intelligence-checkbox" 
                       value="{item['id']}" onchange="toggleRowSelection({item['id']}, this)">
            </td>
            <td class="title-col">
                <div class="title-wrapper">
                    <a href="{html.escape(source_url)}" target="_blank" class="text-decoration-none intelligence-title-link">
                        <strong class="text-primary">{html.escape(item['title'][:60])}{'...' if len(item['title']) > 60 else ''}</strong>
                    </a>
                    {f'<div class="text-muted small mt-1 intelligence-summary">{display_summary}</div>' if display_summary else ''}
                    <div class="mt-2 intelligence-badges">
                        <span class="badge bg-primary me-1">{topic}</span>
                        <span class="badge bg-info me-1">{category}</span>
                        {competitor_badge}
                        {f'<span class="badge bg-warning">合并{item["merged_count"]}条</span>' if item.get('is_merged') else ''}
                    </div>
                </div>
            </td>
            <td class="text-center time-col">
                <small class="text-muted">{display_time}</small>
            </td>
            <td class="text-center score-col">
                <div class="score-container">
                    <i class="bi bi-{score_icon} text-{score_class} me-1"></i>
                    <span class="badge bg-{score_class} ai-score-badge cursor-help" 
                          data-bs-toggle="tooltip" 
                          data-bs-placement="top"
                          data-bs-html="true"
                          title="{tooltip_content}">{score:.1f}</span>
                </div>
            </td>
            <td class="status-col">
                <select class="form-select form-select-sm status-select" 
                        onchange="updateQualityStatus({item['id']}, this.value)"
                        data-current="{status}">
                    {all_status_options}
                </select>
            </td>
            <td class="text-center actions-col">
                <div class="btn-group-custom" role="group">
                    <button type="button" class="btn btn-outline-primary btn-sm btn-action" 
                            onclick="viewDetails({item['id']})" 
                            title="查看详情"
                            data-bs-toggle="tooltip">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button type="button" class="btn btn-outline-success btn-sm btn-action" 
                            onclick="aiProcess({item['id']})" 
                            title="AI重新分析"
                            data-bs-toggle="tooltip">
                        <i class="bi bi-robot"></i>
                    </button>
                    <button type="button" class="btn btn-outline-warning btn-sm btn-action" 
                            onclick="editIntelligence({item['id']})" 
                            title="编辑"
                            data-bs-toggle="tooltip">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button type="button" class="btn btn-outline-danger btn-sm btn-action" 
                            onclick="deleteIntelligence({item['id']})" 
                            title="删除"
                            data-bs-toggle="tooltip">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
        """
        rows.append(row)

    # 修复：批量操作工具栏 - 修复导出下拉问题
    batch_toolbar = """
    <div class="batch-operations bg-light p-3 border-bottom">
        <div class="d-flex flex-wrap align-items-center justify-content-between">
            <div class="batch-actions-left d-flex flex-wrap align-items-center">
                <span class="me-3 fw-bold">批量操作:</span>
                <button class="btn btn-success btn-sm me-2 mb-1 batch-btn" onclick="batchAIProcess()">
                    <i class="bi bi-robot"></i> 批量AI分析
                </button>
                <button class="btn btn-primary btn-sm me-2 mb-1 batch-btn" onclick="batchApprove()">
                    <i class="bi bi-check-circle"></i> 批量通过
                </button>
                <button class="btn btn-warning btn-sm me-2 mb-1 batch-btn" onclick="batchReject()">
                    <i class="bi bi-x-circle"></i> 批量拒绝
                </button>
                

                <!-- 修复：导出按钮组 - 移除重复下拉箭头 -->
                <div class="btn-group me-2 mb-1">
                    <button class="btn btn-info btn-sm batch-btn" onclick="exportSelected('csv')">
                        <i class="bi bi-download"></i> 导出选中
                    </button>
                    <button class="btn btn-info btn-sm dropdown-toggle dropdown-toggle-split" 
                            data-bs-toggle="dropdown" 
                            aria-expanded="false">
                        <span class="visually-hidden">更多导出选项</span>
                    </button>
                    <ul class="dropdown-menu">
                        <li><h6 class="dropdown-header">导出选中项</h6></li>
                        <li><a class="dropdown-item" href="#" onclick="exportSelected('csv')">
                            <i class="bi bi-filetype-csv"></i> 导出选中为CSV
                        </a></li>
                        <li><a class="dropdown-item" href="#" onclick="exportSelected('json')">
                            <i class="bi bi-filetype-json"></i> 导出选中为JSON
                        </a></li>
                        <li><hr class="dropdown-divider"></li>
                        <li><h6 class="dropdown-header">导出筛选结果</h6></li>
                        <li><a class="dropdown-item" href="#" onclick="exportFiltered('csv')">
                            <i class="bi bi-funnel"></i> 导出当前筛选结果(CSV)
                        </a></li>
                        <li><a class="dropdown-item" href="#" onclick="exportFiltered('json')">
                            <i class="bi bi-funnel"></i> 导出当前筛选结果(JSON)
                        </a></li>
                        <li><hr class="dropdown-divider"></li>
                        <li><a class="dropdown-item" href="#" onclick="exportAll('csv')">
                            <i class="bi bi-collection"></i> 导出全部数据(CSV)
                        </a></li>
                        <li><a class="dropdown-item" href="#" onclick="downloadTemplate()">
                            <i class="bi bi-file-earmark"></i> 下载导入模板
                        </a></li>
                    </ul>
                </div>

                <button class="btn btn-danger btn-sm me-2 mb-1 batch-btn" onclick="batchDelete()">
                    <i class="bi bi-trash"></i> 批量删除
                </button>
                <button class="btn btn-warning btn-sm me-2 mb-1 batch-btn" onclick="batchExtractDatesForSelected()">
                    <i class="bi bi-calendar-plus"></i> 为选中项补全日期
                </button>
            </div>
            <div class="batch-count-right">
                <span id="selectedCount" class="text-muted">已选择 0 项</span>
            </div>
        </div>
    </div>
    """

    # 分页
    pagination = f"""
    <div class="d-flex justify-content-between align-items-center p-3 bg-light border-top">
        <div>
            <span>共 <strong>{total}</strong> 条记录，第 <strong>{page}</strong> 页，共 <strong>{total_pages}</strong> 页</span>
        </div>
        <div class="d-flex align-items-center">
            <label class="me-2">每页:</label>
            <select class="form-select form-select-sm me-3" style="width: 80px;" onchange="changePageSize(this.value)">
                <option value="10" {'selected' if page_size == 10 else ''}>10</option>
                <option value="20" {'selected' if page_size == 20 else ''}>20</option>
                <option value="50" {'selected' if page_size == 50 else ''}>50</option>
                <option value="100" {'selected' if page_size == 100 else ''}>100</option>
            </select>
            <nav>
                <ul class="pagination pagination-sm mb-0">
                    <li class="page-item {'disabled' if page == 1 else ''}">
                        <a class="page-link" href="#" onclick="{'loadPage(' + str(page - 1) + ')' if page > 1 else 'return false;'}">
                            <i class="bi bi-chevron-left"></i>
                        </a>
                    </li>
                    <li class="page-item active">
                        <span class="page-link">{page}</span>
                    </li>
                    <li class="page-item {'disabled' if page >= total_pages else ''}">
                        <a class="page-link" href="#" onclick="{'loadPage(' + str(page + 1) + ')' if page < total_pages else 'return false;'}">
                            <i class="bi bi-chevron-right"></i>
                        </a>
                    </li>
                </ul>
            </nav>
        </div>
    </div>
    """

    # 完整HTML - 包含所有样式和JS
    html_content = f"""
    <div class="intelligence-table-container">
        {batch_toolbar}

        <div class="table-responsive">
            <table class="table table-hover table-sm mb-0 intelligence-table">
                <thead class="table-light sticky-top">
                    <tr>
                        <th class="text-center checkbox-col">
                            <input type="checkbox" class="form-check-input" id="selectAll" 
                                   onchange="toggleSelectAll(this)">
                        </th>
                        <th class="title-col">标题 / 摘要 / 标签</th>
                        <th class="text-center time-col">新闻时间</th>
                        <th class="text-center score-col">AI评分</th>
                        <th class="text-center status-col">审核状态</th>
                        <th class="text-center actions-col">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
        </div>

        {pagination}
    </div>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap-icons/1.10.0/font/bootstrap-icons.min.css" rel="stylesheet">

    <style>
    /* 修复导出按钮样式 - 解决双下拉问题 */
    .batch-operations .btn-group {{
        position: relative;
    }}

    .batch-operations .btn-group .dropdown-toggle-split {{
        border-left: 1px solid rgba(255,255,255,0.2);
        padding-left: 6px !important;
        padding-right: 6px !important;
    }}

    .batch-operations .dropdown-menu {{
        z-index: 1050;
        min-width: 250px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }}

    .batch-operations .dropdown-item {{
        padding: 8px 16px;
        font-size: 13px;
        display: flex;
        align-items: center;
        gap: 8px;
    }}

    .batch-operations .dropdown-header {{
        font-size: 11px;
        font-weight: 600;
        color: #6c757d;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}

    /* 批量操作栏布局修复 */
    .batch-operations {{
        background: linear-gradient(135deg, #f8f9fa, #e9ecef);
        border-bottom: 2px solid #dee2e6;
        padding: 12px 20px;
    }}

    .batch-actions-left {{
        flex: 1;
        min-width: 0;
    }}

    .batch-count-right {{
        flex-shrink: 0;
        margin-left: 16px;
    }}

    /* 修复批量按钮样式 */
    .batch-btn {{
        font-size: 13px !important;
        padding: 6px 12px !important;
        border-radius: 6px !important;
        font-weight: 500 !important;
        border: 1px solid transparent !important;
        transition: all 0.2s ease !important;
        white-space: nowrap !important;
        display: inline-flex !important;
        align-items: center !important;
        gap: 4px !important;
    }}

    .batch-btn:hover {{
        transform: translateY(-1px) !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15) !important;
    }}

    /* 竞争对手标记样式 */
    .badge[onclick] {{
        cursor: pointer !important;
        transition: all 0.2s ease !important;
    }}

    .badge[onclick]:hover {{
        transform: scale(1.05) !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2) !important;
    }}

    /* 表格布局修复 */
    .intelligence-table {{
        table-layout: fixed;
        width: 100%;
    }}

    .checkbox-col {{ width: 50px; }}
    .title-col {{ width: 40%; }}
    .time-col {{ width: 120px; }}
    .score-col {{ width: 100px; }}
    .status-col {{ width: 130px; }}
    .actions-col {{ width: 200px; }}

    /* 标题列内容处理 */
    .title-wrapper {{
        max-width: 100%;
        overflow: hidden;
    }}

    .intelligence-title-link strong {{
        display: block;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }}

    .intelligence-summary {{
        font-size: 12px;
        line-height: 1.3;
        color: #6c757d;
        margin-top: 4px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }}

    /* 状态选择框修复 */
    .status-select {{
        font-size: 12px !important;
        padding: 4px 8px !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 4px !important;
        background-color: white !important;
        width: 100% !important;
        appearance: menulist !important;
    }}

    .status-select:focus {{
        border-color: #667eea !important;
        box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.1) !important;
        outline: none !important;
    }}

    /* 移动端适配 */
    @media (max-width: 768px) {{
        .batch-operations .d-flex {{
            flex-direction: column;
            gap: 12px;
            align-items: stretch !important;
        }}

        .batch-actions-left {{
            justify-content: center;
        }}

        .batch-count-right {{
            text-align: center;
            margin-left: 0;
        }}

        .intelligence-table {{
            min-width: 900px;
        }}
    }}
    </style>

    <script>
    document.addEventListener('DOMContentLoaded', function() {{
        // 初始化工具提示
        var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {{
            return new bootstrap.Tooltip(tooltipTriggerEl, {{
                html: true,
                container: 'body',
                sanitize: false,
                delay: {{ show: 500, hide: 100 }},
                placement: 'top'
            }});
        }});

        console.log('表格HTML加载完成，工具提示已初始化');
        updateSelectedCount();
    }});

    function updateSelectedCount() {{
        const selected = document.querySelectorAll('.intelligence-checkbox:checked').length;
        const counter = document.getElementById('selectedCount');
        if (counter) {{
            counter.textContent = `已选择 ${{selected}} 项`;
        }}

        const selectAll = document.getElementById('selectAll');
        const totalCheckboxes = document.querySelectorAll('.intelligence-checkbox').length;
        if (selectAll) {{
            selectAll.checked = selected === totalCheckboxes && totalCheckboxes > 0;
            selectAll.indeterminate = selected > 0 && selected < totalCheckboxes;
        }}
    }}

    // 竞争对手标记切换函数
    async function toggleCompetitor(intelligenceId) {{
        try {{
            const response = await fetch(`/api/intelligence/${{intelligenceId}}/toggle-competitor`, {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }}
            }});

            const result = await response.json();
            if (result.status === 'success') {{
                showAlert(result.message, 'success');
                loadTableData(); // 重新加载表格
            }} else {{
                showAlert('操作失败: ' + result.message, 'danger');
            }}
        }} catch (error) {{
            showAlert('操作失败: ' + error.message, 'danger');
        }}
    }}

    window.updateSelectedCount = updateSelectedCount;
    window.toggleCompetitor = toggleCompetitor;
    </script>
    """

    return html_content


# 添加竞争对手切换的API路由
@api_router.post("/{intelligence_id}/toggle-competitor")
async def toggle_competitor_status(
        intelligence_id: int,
        db: AsyncSession = Depends(get_db)
):
    """切换情报的竞争对手标记状态"""
    try:
        result = await db.execute(
            select(Intelligence).where(Intelligence.id == intelligence_id)
        )
        intelligence = result.scalar_one_or_none()

        if not intelligence:
            raise HTTPException(status_code=404, detail="情报不存在")

        # 简单的竞争对手标记逻辑：使用一个字段存储
        # 这里假设有一个 is_competitor 字段，如果没有可以添加到模型中
        # 或者使用现有的字段存储这个信息

        # 临时方案：在 score_dimensions 中存储竞争对手信息
        score_dimensions = {}
        if intelligence.score_dimensions:
            try:
                import json
                score_dimensions = json.loads(intelligence.score_dimensions)
            except:
                score_dimensions = {}

        # 切换竞争对手状态
        is_competitor = score_dimensions.get('is_competitor', False)
        score_dimensions['is_competitor'] = not is_competitor

        intelligence.score_dimensions = json.dumps(score_dimensions, ensure_ascii=False)
        intelligence.update_time = datetime.now()

        await db.commit()

        status_text = "已标记为竞争对手" if not is_competitor else "已取消竞争对手标记"

        return {
            "status": "success",
            "message": status_text,
            "is_competitor": not is_competitor
        }

    except Exception as e:
        await db.rollback()
        return {"status": "error", "message": str(e)}


# ===== 页面路由 =====

@pages_router.get("/", response_class=HTMLResponse)
async def intelligence_page(request: Request):
    """情报管理主页面"""
    return templates.TemplateResponse("intelligence.html", {"request": request})