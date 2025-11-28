import streamlit as st
from sqlmodel import Session, select, delete, desc, asc
from sqlalchemy import func
from models import SiteConfig, Article, GlobalSettings
from logic import init_db, engine, crawl_all_sites, analyze_specific_articles, auto_detect_config, test_crawler_config
import asyncio
import pandas as pd
import json

st.set_page_config(page_title="AI 智能情报系统", layout="wide")
init_db()

st.title("🚀 AI 智能情报系统")

if "select_all" not in st.session_state: st.session_state.select_all = False
if "auto_config" not in st.session_state: st.session_state.auto_config = {}

tab_dashboard, tab_sources, tab_settings = st.tabs(["📊 情报看板", "⚙️ 来源管理", "🛠️ 系统设置"])

# ==========================================
# Tab 1: 情报看板
# ==========================================
with tab_dashboard:
    with st.expander("🔎 采集控制", expanded=False):
        c1, c2, c3, c4 = st.columns([1, 1, 2, 2])
        days_back = c1.number_input("爬取天数", 1, 30, 3)
        max_pages = c2.number_input("翻页限制", 1, 20, 5)

        with Session(engine) as session:
            active_sites = session.exec(select(SiteConfig).where(SiteConfig.is_active == True)).all()
            site_count = len(active_sites)

        c3.write("")
        c3.write("")
        if c3.button(f"🕷️ 爬取 {site_count} 个启用源", type="primary", width='stretch',
                     disabled=site_count == 0):
            with st.status("全网爬取中..."):
                site_ids = [s.id for s in active_sites]
                stats = asyncio.run(crawl_all_sites(site_ids, days_back, max_pages))
                st.write("--- 报告 ---")
                st.write(f"总爬取: {stats['total_crawled']} | 新增: {stats['new_added']}")
            st.success("完成")
            st.rerun()

    st.divider()

    c_f1, c_f2, c_f3, c_f4 = st.columns([1, 1, 1, 1])

    with Session(engine) as session:
        all_sites = session.exec(select(SiteConfig)).all()
        f_site = c_f1.multiselect("来源筛选", [s.name for s in all_sites])
        f_status = c_f2.multiselect("状态筛选", ["done", "pending", "error"], default=["done", "pending"])
        f_score = c_f3.slider("最低评分过滤", 0, 10, 0)

        if c_f4.button("🗑️ 清空所有文章数据", type="secondary"):
            session.exec(delete(Article))
            session.commit()
            st.rerun()

        query = select(Article)
        if f_status: query = query.where(Article.ai_status.in_(f_status))
        if f_site:
            s_ids = [s.id for s in all_sites if s.name in f_site]
            query = query.where(Article.site_id.in_(s_ids))
        if f_score > 0:
            query = query.where(Article.ai_score >= f_score)

        query = query.order_by(desc(Article.crawled_at))
        articles = session.exec(query).all()

    if not articles:
        st.info("暂无数据")
    else:
        c_sel, c_act = st.columns([1, 5])
        if c_sel.button("✅ 全选/取消全选"):
            st.session_state.select_all = not st.session_state.select_all
            st.rerun()

        data_list = []
        for a in articles:
            s_name = next((s.name for s in all_sites if s.id == a.site_id), "未知")
            pub_date = a.publish_date.strftime("%Y-%m-%d") if a.publish_date else ""
            data_list.append({
                "选择": st.session_state.select_all,
                "ID": a.id, "来源": s_name, "中文标题": a.new_title if a.new_title else a.title,
                "英文标题": a.title_en,
                "日期": pub_date, "分数": a.ai_score, "摘要": a.ai_summary,
                "理由": a.ai_reasoning, "链接": a.url, "状态": a.ai_status,
                "议题": a.ai_topic, "类别": a.ai_category
            })

        df = pd.DataFrame(data_list)
        edited_df = st.data_editor(
            df,
            column_config={
                "选择": st.column_config.CheckboxColumn(required=True, width="small"),
                "链接": st.column_config.LinkColumn("原文"),
                "分数": st.column_config.ProgressColumn("价值", min_value=0, max_value=10, format="%d"),
            },
            hide_index=True, width='stretch', height=600
        )

        selected_ids = edited_df[edited_df["选择"] == True]["ID"].tolist()
        col_do1, col_do2 = st.columns([1, 4])
        with col_do1:
            if st.button(f"🧠 分析选中 ({len(selected_ids)})", type="primary", disabled=len(selected_ids) == 0):
                with st.status("AI 分析中..."):
                    progress = st.progress(0)
                    asyncio.run(analyze_specific_articles(selected_ids))
                    progress.progress(100)
                st.success("分析完成")
                st.rerun()
        with col_do2:
            csv = edited_df.drop(columns=["选择"]).to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出表格 CSV", csv, "report.csv", "text/csv")

# ==========================================
# Tab 2: 来源管理 (修复：动态 Key 解决刷新问题)
# ==========================================

with tab_sources:
    st.subheader("情报源管理")
    st.write("DEBUG auto_config:", st.session_state.auto_config)
    # 1. 列表展示
    with Session(engine) as session:
        sites = session.exec(select(SiteConfig)).all()
        if sites:
            df_sites = pd.DataFrame([{
                "ID": s.id, "启用": s.is_active, "名称": s.name, "URL": s.url,
                "上次更新": s.created_at.strftime("%Y-%m-%d")
            } for s in sites])
            st.dataframe(df_sites, width='stretch', hide_index=True)

    st.divider()

    # 2. 编辑/新建 选择逻辑
    options = ["➕ 新建情报源"] + [f"{s.name} (ID:{s.id})" for s in sites]


    # 使用 callback 清空 AI 缓存，确保切换时数据干净
    def on_source_change():
        st.session_state.auto_config = {}


    selected_option = st.selectbox("选择操作对象", options, on_change=on_source_change)

    current_site_id = None
    # 默认值
    form_vals = {"name": "", "url": "", "active": True, "list": "", "title": "", "link": "", "date": "", "fmt": "",
                 "next": ""}

    # 如果选了已有的，填充值
    if selected_option != "➕ 新建情报源":
        import re

        match = re.search(r"ID:(\d+)", selected_option)
        if match:
            current_site_id = int(match.group(1))
            with Session(engine) as session:
                s = session.get(SiteConfig, current_site_id)
                if s:
                    form_vals = {
                        "name": s.name, "url": s.url, "active": s.is_active,
                        "list": s.list_selector, "title": s.title_selector, "link": s.link_selector,
                        "date": s.date_selector or "", "fmt": s.date_format or "", "next": s.next_page_selector or ""
                    }

    # === 关键：生成动态 Key 后缀 ===
    # 如果是新建，后缀是 "new"；如果是编辑 ID=5，后缀是 "5"
    # 这样 Streamlit 就会把它们视为不同的输入框，强制刷新值
    k_suffix = str(current_site_id) if current_site_id else "new"
    st.write("DEBUG k_suffix:", k_suffix)

    # AI 识别结果覆盖
    ac = st.session_state.auto_config
    # 确保所有字段都能从auto_config中正确获取值
    val_url = ac.get("url", form_vals["url"])
    val_list = ac.get("list", form_vals["list"])
    val_title = ac.get("title", form_vals["title"])
    val_link = ac.get("link", form_vals["link"])
    val_date = ac.get("date", form_vals["date"])
    val_fmt = ac.get("date_format", form_vals["fmt"])
    val_next = ac.get("next_page", form_vals["next"])

    c_edit, c_test = st.columns([1, 1])

    with c_edit:
        st.subheader("配置表单")

        c_tool1, c_tool2 = st.columns([3, 1])
        with c_tool1:
            # 动态 Key：key=f"u_in_{k_suffix}"
            u_in = st.text_input("URL (输入后点识别)", value=val_url, key=f"u_in_{k_suffix}")
        with c_tool2:
            st.write("")
            st.write("")
            if st.button("🪄 AI 识别", key=f"btn_ai_{k_suffix}"):
                if not u_in:
                    st.error("请输入URL")
                else:
                    with st.spinner("AI 识别中..."):
                        res = asyncio.run(auto_detect_config(u_in))
                        st.write("DEBUG AI 返回:", res)
                        if "error" in res:
                            st.error(res["error"])
                        else:
                            st.session_state.auto_config = res
                            print(f"[DEBUG] AI Result: {res}")
                            st.success("识别成功")
                            st.rerun()

        name = st.text_input("名称", value=form_vals["name"], key=f"name_{k_suffix}")
        is_active = st.checkbox("启用此源", value=form_vals["active"], key=f"active_{k_suffix}")

        list_s = st.text_input("List Selector", value=val_list, key=f"list_{k_suffix}")
        title_s = st.text_input("Title CSS", value=val_title, key=f"title_{k_suffix}")
        link_s = st.text_input("Link CSS", value=val_link, key=f"link_{k_suffix}")
        date_s = st.text_input("Date CSS", value=val_date, key=f"date_{k_suffix}")
        date_fmt = st.text_input("Date Format", value=val_fmt, key=f"fmt_{k_suffix}")
        next_s = st.text_input("Next Page CSS", value=val_next, key=f"next_{k_suffix}")

        c_b1, c_b2 = st.columns([1, 1])
        if c_b1.button("💾 保存/更新", type="primary", key=f"save_{k_suffix}"):
            with Session(engine) as session:
                if current_site_id:  # 更新
                    s = session.get(SiteConfig, current_site_id)
                    s.name, s.url, s.is_active = name, u_in, is_active
                    s.list_selector, s.title_selector, s.link_selector = list_s, title_s, link_s
                    s.date_selector, s.date_format, s.next_page_selector = date_s, date_fmt, next_s
                    session.add(s)
                    msg = "已更新"
                else:  # 新建
                    s = SiteConfig(
                        name=name, url=u_in, is_active=is_active,
                        list_selector=list_s, title_selector=title_s, link_selector=link_s,
                        date_selector=date_s, date_format=date_fmt, next_page_selector=next_s
                    )
                    session.add(s)
                    msg = "已新建"
                session.commit()
            st.success(msg)
            st.session_state.auto_config = {}
            st.rerun()

        if current_site_id and c_b2.button("🗑️ 删除此源", key=f"del_{k_suffix}"):
            with Session(engine) as session:
                obj = session.get(SiteConfig, current_site_id)
                session.delete(obj)
                session.commit()
            st.success("已删除")
            st.rerun()

    with c_test:
        st.subheader("🧪 配置测试")
        st.info("验证配置是否有效。")
        if st.button("① 测试提取 (抓取第1页)", key=f"test1_{k_suffix}"):
            if not u_in or not list_s:
                st.error("请完善配置")
            else:
                with st.spinner("正在抓取首页..."):
                    test_url_1 = u_in.replace("{n}", "1")
                    selectors = {"list": list_s, "title": title_s, "link": link_s, "date": date_s}
                    res = asyncio.run(test_crawler_config(test_url_1, selectors))
                    if res['success']:
                        st.success(f"✅ 成功！抓取到 {res['count']} 条。")
                        with st.expander("查看数据", expanded=True):
                            st.json(res['data'])
                    else:
                        st.error(res['error'])

        st.write("")
        if st.button("② 验证分页 (尝试抓取前2页)", key=f"test2_{k_suffix}"):
            if not u_in or not list_s:
                st.error("请完善配置")
            else:
                from logic import test_pagination_logic

                with st.spinner("尝试翻页..."):
                    selectors = {"list": list_s, "title": title_s, "next_page": next_s}
                    report = asyncio.run(test_pagination_logic(u_in, selectors))

                    st.info(f"模式: **{report['mode']}**")
                    for p in report['pages']:
                        if isinstance(p, str):
                            st.error(p)
                        else:
                            st.write(f"📄 **第 {p['page']} 页**: 抓到 {p['item_count']} 条")
                            if "next_button_found" in p:
                                st.caption(p["next_button_found"])

# ==========================================
# Tab 3: 系统设置
# ==========================================
with tab_settings:
    st.header("🛠️ 系统设置")
    with Session(engine) as session:
        settings = session.exec(select(GlobalSettings)).first()
        if not settings:
            settings = GlobalSettings()
            session.add(settings)
            session.commit()

        with st.form("set_form"):
            client = st.text_area("客户画像", value=settings.client_profile)
            c1, c2 = st.columns(2)
            try:
                comps = json.loads(settings.competitors_json)
                cn = "\n".join(comps.get("中文名", []))
                en = "\n".join(comps.get("英文名", []))
            except:
                cn, en = "", ""
            new_cn = c1.text_area("竞争对手(中)", value=cn)
            new_en = c2.text_area("竞争对手(英)", value=en)
            topics = st.text_area("议题 (JSON)", value=settings.topics_json)
            cats = st.text_area("类别 (JSON)", value=settings.categories_json)
            if st.form_submit_button("保存设置"):
                cn_l = [x.strip() for x in new_cn.split('\n') if x.strip()]
                en_l = [x.strip() for x in new_en.split('\n') if x.strip()]
                new_json = json.dumps({"中文名": cn_l, "英文名": en_l}, ensure_ascii=False)
                settings.client_profile = client
                settings.competitors_json = new_json
                settings.topics_json = topics
                settings.categories_json = cats
                session.add(settings)
                session.commit()
                st.success("已保存")