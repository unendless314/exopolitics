import html
import json
import pathlib

from .config import load_config
from .database import get_connection

def generate_inspector_html(
    db_path: pathlib.Path,
    config_dir: pathlib.Path,
    out_path: pathlib.Path,
    item_limit: int = 500
) -> None:
    """
    Queries canonical.db and loads source configuration to generate a beautiful,
    self-contained, interactive HTML Ingest Inspector dashboard.
    """
    # 1. Check if database exists
    if not db_path.exists():
        raise FileNotFoundError(f"Database does not exist at: {db_path}. Please run migrations or fetches first.")

    # 2. Load configuration definitions
    config = load_config(config_dir)
    sources_by_id = {s.id: s for s in config.sources}
    categories_by_id = {c.id: c for c in config.categories.values()}

    # Initialize stats
    total_sources = len(config.sources)
    healthy_count = 0
    degraded_count = 0
    quarantined_count = 0
    total_items = 0
    
    source_states_dict = {}
    recent_runs = []
    items_list = []

    # 3. Query Database
    conn = get_connection(db_path)
    try:
        # Fetch Total Items count
        cursor = conn.execute("SELECT COUNT(*) FROM source_item")
        total_items = cursor.fetchone()[0]

        # Fetch Source States
        cursor = conn.execute(
            "SELECT source_id, health_status, last_fetch_at, last_success_at, "
            "consecutive_failures, last_error_class, last_error_at, quarantine_until FROM source_state"
        )
        for row in cursor.fetchall():
            r_dict = dict(row)
            s_id = r_dict["source_id"]
            source_states_dict[s_id] = r_dict

            status = r_dict["health_status"]
            if status == "healthy":
                healthy_count += 1
            elif status == "degraded":
                degraded_count += 1
            elif status == "quarantined":
                quarantined_count += 1

        # Fetch Recent Runs
        cursor = conn.execute(
            "SELECT fetch_run_id, started_at, ended_at, run_scope, trigger_type, run_status, "
            "due_source_count, attempted_source_count, succeeded_source_count, failed_source_count, error_summary "
            "FROM fetch_run ORDER BY fetch_run_id DESC LIMIT 15"
        )
        recent_runs = [dict(row) for row in cursor.fetchall()]

        # Fetch Source Items
        cursor = conn.execute(
            "SELECT source_item_id, source_id, title, summary, canonical_url, published_at, fetched_at, dedup_rule "
            "FROM source_item ORDER BY fetched_at DESC LIMIT ?",
            (item_limit,)
        )
        items_list = [dict(row) for row in cursor.fetchall()]

    except Exception as e:
        # Let database errors propagate upward to trigger non-zero exit codes in the CLI
        raise RuntimeError(f"Database query failure: {str(e)}") from e
    finally:
        conn.close()

    # Calculate status from config if db didn't populate it yet
    for s_id, s in sources_by_id.items():
        if s_id not in source_states_dict:
            source_states_dict[s_id] = {
                "source_id": s_id,
                "health_status": "healthy" if s.enabled else "disabled",
                "last_fetch_at": None,
                "last_success_at": None,
                "consecutive_failures": 0,
                "last_error_class": None,
                "last_error_at": None,
                "quarantine_until": None
            }
            if s.enabled:
                healthy_count += 1

    # Map categories and titles to recent items list
    for item in items_list:
        s_id = item["source_id"]
        source_conf = sources_by_id.get(s_id)
        if source_conf:
            item["source_title"] = source_conf.title
            item["source_html_url"] = source_conf.html_url
            cat = categories_by_id.get(source_conf.category_id)
            item["category_name"] = cat.name if cat else f"Cat {source_conf.category_id}"
        else:
            item["source_title"] = f"Unknown Source (ID: {s_id})"
            item["source_html_url"] = None
            item["category_name"] = "Unknown"

    # 4. Generate HTML (with robust escaping of all dynamically generated strings)
    html_template = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UAP Aggregation System - Ingest Inspector</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-green: #10b981;
            --accent-green-glow: rgba(16, 185, 129, 0.15);
            --accent-red: #ef4444;
            --accent-red-glow: rgba(239, 68, 68, 0.15);
            --accent-amber: #f59e0b;
            --accent-amber-glow: rgba(245, 158, 11, 0.15);
            --accent-blue: #3b82f6;
            --accent-blue-glow: rgba(59, 130, 246, 0.15);
            --transition-speed: 0.2s;
        }}

        body {{
            font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            margin: 0;
            padding: 0;
            line-height: 1.5;
        }}

        header {{
            background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%);
            padding: 2rem 2.5rem;
            border-bottom: 1px solid var(--card-border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        h1, h2, h3 {{
            font-family: 'Outfit', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
        }}

        h1 {{
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            background: linear-gradient(to right, #818cf8, #c084fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .subtitle {{
            color: var(--text-secondary);
            margin-top: 0.35rem;
            font-size: 0.9rem;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }}

        /* Stats Grid */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }}

        .stat-card {{
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
            transition: transform var(--transition-speed) ease, border-color var(--transition-speed) ease;
        }}

        .stat-card:hover {{
            transform: translateY(-2px);
        }}

        .stat-card.healthy {{ border-left: 4px solid var(--accent-green); box-shadow: 0 4px 20px -2px var(--accent-green-glow); }}
        .stat-card.quarantined {{ border-left: 4px solid var(--accent-red); box-shadow: 0 4px 20px -2px var(--accent-red-glow); }}
        .stat-card.runs {{ border-left: 4px solid var(--accent-blue); box-shadow: 0 4px 20px -2px var(--accent-blue-glow); }}
        .stat-card.items {{ border-left: 4px solid var(--accent-amber); box-shadow: 0 4px 20px -2px var(--accent-amber-glow); }}

        .stat-value {{
            font-size: 2.2rem;
            font-weight: 700;
            font-family: 'Outfit', sans-serif;
            margin-top: 0.5rem;
        }}

        .stat-label {{
            color: var(--text-secondary);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }}

        /* Tabs Section */
        .tabs {{
            display: flex;
            gap: 1rem;
            border-bottom: 1px solid var(--card-border);
            margin-bottom: 2rem;
        }}

        .tab-btn {{
            background: none;
            border: none;
            color: var(--text-secondary);
            font-family: 'Outfit', sans-serif;
            font-size: 1.05rem;
            font-weight: 500;
            padding: 0.75rem 1.25rem;
            cursor: pointer;
            position: relative;
            transition: color var(--transition-speed);
        }}

        .tab-btn:hover {{
            color: var(--text-primary);
        }}

        .tab-btn.active {{
            color: #818cf8;
        }}

        .tab-btn.active::after {{
            content: '';
            position: absolute;
            bottom: -1px;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(to right, #818cf8, #c084fc);
            border-radius: 3px 3px 0 0;
        }}

        .tab-content {{
            display: none;
        }}

        .tab-content.active {{
            display: block;
        }}

        /* Search & Filters */
        .filter-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
            margin-bottom: 1.5rem;
            background-color: #1e293b80;
            padding: 1rem;
            border-radius: 8px;
            border: 1px solid var(--card-border);
        }}

        .search-input {{
            flex: 1;
            min-width: 280px;
            background-color: #0f172a;
            border: 1px solid var(--card-border);
            color: var(--text-primary);
            padding: 0.65rem 1rem;
            border-radius: 6px;
            font-size: 0.9rem;
            transition: border-color var(--transition-speed);
        }}

        .search-input:focus {{
            border-color: #818cf8;
            outline: none;
        }}

        .select-filter {{
            background-color: #0f172a;
            border: 1px solid var(--card-border);
            color: var(--text-primary);
            padding: 0.65rem 1.5rem 0.65rem 1rem;
            border-radius: 6px;
            font-size: 0.9rem;
            cursor: pointer;
        }}

        .select-filter:focus {{
            border-color: #818cf8;
            outline: none;
        }}

        /* Tables & Lists */
        .table-container {{
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            overflow-x: auto;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
            margin-bottom: 2rem;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.85rem;
        }}

        th {{
            background-color: #0f172a;
            color: var(--text-secondary);
            font-family: 'Outfit', sans-serif;
            font-weight: 600;
            padding: 1rem 1.25rem;
            border-bottom: 1px solid var(--card-border);
            letter-spacing: 0.02em;
            white-space: nowrap;
        }}

        td {{
            padding: 0.85rem 1.25rem;
            border-bottom: 1px solid var(--card-border);
            color: var(--text-primary);
            vertical-align: middle;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        tr:hover td {{
            background-color: #33415533;
        }}

        /* Badges */
        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 0.2rem 0.5rem;
            border-radius: 9999px;
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}

        .badge-success {{ background-color: rgba(16, 185, 129, 0.1); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.2); }}
        .badge-error {{ background-color: rgba(239, 68, 68, 0.1); color: var(--accent-red); border: 1px solid rgba(239, 68, 68, 0.2); }}
        .badge-warning {{ background-color: rgba(245, 158, 11, 0.1); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.2); }}
        .badge-info {{ background-color: rgba(59, 130, 246, 0.1); color: var(--accent-blue); border: 1px solid rgba(59, 130, 246, 0.2); }}
        .badge-gray {{ background-color: rgba(148, 163, 184, 0.1); color: var(--text-secondary); border: 1px solid rgba(148, 163, 184, 0.2); }}

        /* Ingested Items Cards */
        .items-list {{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}

        .item-card {{
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 1.25rem 1.5rem;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
            transition: border-color var(--transition-speed);
        }}

        .item-card:hover {{
            border-color: #4f46e5;
        }}

        .item-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
            margin-bottom: 0.5rem;
        }}

        .item-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #818cf8;
            text-decoration: none;
            transition: color var(--transition-speed);
        }}

        .item-title:hover {{
            color: #c084fc;
            text-decoration: underline;
        }}

        .item-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 1.2rem;
            color: var(--text-secondary);
            font-size: 0.8rem;
            margin-bottom: 0.75rem;
            border-bottom: 1px solid #33415540;
            padding-bottom: 0.5rem;
        }}

        .meta-group {{
            display: flex;
            align-items: center;
            gap: 0.25rem;
        }}

        .meta-label {{
            font-weight: 500;
            color: #64748b;
        }}

        details {{
            background-color: #0f172a80;
            border-radius: 6px;
            padding: 0.5rem 1rem;
            border: 1px solid #33415530;
        }}

        summary {{
            cursor: pointer;
            font-weight: 600;
            font-size: 0.8rem;
            color: var(--text-secondary);
            user-select: none;
            outline: none;
        }}

        summary:hover {{
            color: var(--text-primary);
        }}

        .summary-content {{
            margin-top: 0.5rem;
            font-size: 0.875rem;
            color: #cbd5e1;
            white-space: pre-wrap;
            line-height: 1.6;
        }}

        .no-data {{
            text-align: center;
            padding: 3rem;
            color: var(--text-secondary);
            font-style: italic;
        }}

        .info-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin-bottom: 1.5rem;
        }}
    </style>
</head>
<body>
    <header>
        <div>
            <h1>UAP Aggregation System</h1>
            <div class="subtitle">Ingest Module - Local Database Inspector</div>
        </div>
        <div style="text-align: right; color: var(--text-secondary); font-size: 0.8rem;">
            <div>資料庫路徑: <code>{html.escape(db_path.name)}</code></div>
            <div>產生時間: {get_utc_now_iso()}</div>
        </div>
    </header>

    <div class="container">
        <!-- Stats Grid -->
        <div class="stats-grid">
            <div class="stat-card items">
                <div class="stat-label">已抓取原始文章數 (Total Items)</div>
                <div class="stat-value">{total_items}</div>
            </div>
            <div class="stat-card healthy">
                <div class="stat-label">健康來源數 (Healthy Sources)</div>
                <div class="stat-value">{healthy_count} <span style="font-size: 1rem; font-weight: normal; color: var(--text-secondary);">/ {total_sources}</span></div>
            </div>
            <div class="stat-card quarantined">
                <div class="stat-label">已隔離/異常 (Quarantined/Degraded)</div>
                <div class="stat-value">{quarantined_count + degraded_count}</div>
            </div>
            <div class="stat-card runs">
                <div class="stat-label">最近批次執行 (Recent Fetch Runs)</div>
                <div class="stat-value">{len(recent_runs)}</div>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab(this, 'articles-tab')">已抓取文章列表 ({len(items_list)})</button>
            <button class="tab-btn" onclick="switchTab(this, 'sources-tab')">RSS 來源狀態表 ({total_sources})</button>
            <button class="tab-btn" onclick="switchTab(this, 'runs-tab')">最近抓取紀錄 ({len(recent_runs)})</button>
        </div>

        <!-- TAB 1: ARTICLES -->
        <div id="articles-tab" class="tab-content active">
            <div class="filter-bar">
                <input type="text" id="search-box" class="search-input" placeholder="即時搜尋文章標題或摘要..." oninput="filterArticles()">
                <select id="source-filter" class="select-filter" onchange="filterArticles()">
                    <option value="">所有來源 (All Sources)</option>
                    {"".join(f'<option value="{s.id}">{html.escape(s.title)}</option>' for s in config.sources)}
                </select>
                <select id="category-filter" class="select-filter" onchange="filterArticles()">
                    <option value="">所有主題 (All Categories)</option>
                    {"".join(f'<option value="{c.id}">{html.escape(c.name)}</option>' for c in config.categories.values())}
                </select>
            </div>

            <div class="info-bar">
                <div>最新抓取排序 (Latest Ingested first, Limit: {item_limit} 筆)</div>
                <div id="search-result-count">正在載入...</div>
            </div>

            <div class="items-list" id="articles-container">
    """

    # Add individual items to HTML
    if not items_list:
        html_template += '<div class="no-data">資料庫中目前沒有已抓取的文章條目。</div>'
    else:
        for idx, item in enumerate(items_list):
            summary_content = item["summary"] or "此文章無摘要內容。"
            
            # Escape HTML safe values
            title_escaped = html.escape(item["title"])
            summary_escaped = html.escape(summary_content)
            source_title_escaped = html.escape(item["source_title"])
            category_name_escaped = html.escape(item["category_name"])
            search_title = html.escape(json.dumps(item["title"].lower())[1:-1], quote=True)
            search_summary = html.escape(json.dumps(summary_content.lower())[1:-1], quote=True)
            
            published_val = html.escape(item["published_at"] or "未知發布時間")
            fetched_val = html.escape(item["fetched_at"] or "未知抓取時間")
            dedup_rule_escaped = html.escape(item["dedup_rule"])
            source_id_escaped = html.escape(str(item["source_id"]))
            
            # Category ID search attribute
            source_conf = sources_by_id.get(item["source_id"])
            cat_id_escaped = html.escape(str(source_conf.category_id if source_conf else ''))
            
            if item["canonical_url"]:
                url_escaped = html.escape(item["canonical_url"], quote=True)
                link_html = f'<a href="{url_escaped}" target="_blank" class="item-title">{title_escaped}</a>'
            else:
                link_html = f'<span class="item-title" style="color: var(--text-primary);">{title_escaped}</span>'
            
            html_template += f"""
                <div class="item-card" data-title="{search_title}" data-summary="{search_summary}" data-source-id="{source_id_escaped}" data-category-id="{cat_id_escaped}">
                    <div class="item-header">
                        {link_html}
                        <span class="badge badge-info">{category_name_escaped}</span>
                    </div>
                    <div class="item-meta">
                        <div class="meta-group">
                            <span class="meta-label">來源:</span>
                            <span style="color: var(--text-primary); font-weight: 500;">{source_title_escaped}</span>
                        </div>
                        <div class="meta-group">
                            <span class="meta-label">發布時間:</span>
                            <span>{published_val}</span>
                        </div>
                        <div class="meta-group">
                            <span class="meta-label">抓取時間:</span>
                            <span>{fetched_val}</span>
                        </div>
                        <div class="meta-group">
                            <span class="meta-label">去重規則:</span>
                            <span class="badge badge-gray">{dedup_rule_escaped}</span>
                        </div>
                    </div>
                    <details>
                        <summary>檢視文章摘要 / 內容大綱</summary>
                        <div class="summary-content">{summary_escaped}</div>
                    </details>
                </div>
            """

    html_template += """
            </div>
        </div>

        <!-- TAB 2: SOURCES -->
        <div id="sources-tab" class="tab-content">
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 50px;">ID</th>
                            <th>來源標題 (Source Title)</th>
                            <th>主題分類</th>
                            <th>健康狀態</th>
                            <th>連續失敗數</th>
                            <th>最後成功時間</th>
                            <th>最後錯誤類型</th>
                            <th>隔離截止時間</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    # Add sources list to HTML
    for s in config.sources:
        state = source_states_dict.get(s.id, {
            "health_status": "healthy" if s.enabled else "disabled",
            "consecutive_failures": 0,
            "last_success_at": None,
            "last_error_class": None,
            "quarantine_until": None
        })

        health_status = state["health_status"]
        if not s.enabled:
            health_status = "disabled"
            badge_class = "badge-gray"
        elif health_status == "healthy":
            badge_class = "badge-success"
        elif health_status == "degraded":
            badge_class = "badge-warning"
        else:
            badge_class = "badge-error"

        cat = categories_by_id.get(s.category_id)
        cat_name = cat.name if cat else f"Cat {s.category_id}"

        title_escaped = html.escape(s.title)
        xml_url_escaped = html.escape(s.xml_url, quote=True)
        html_url_escaped = html.escape(s.html_url or "", quote=True)
        cat_name_escaped = html.escape(cat_name)
        health_escaped = html.escape(health_status)
        badge_class_escaped = html.escape(badge_class)
        consecutive_failures = state["consecutive_failures"]
        
        last_success_escaped = html.escape(state["last_success_at"] or "-")
        last_error_class_escaped = html.escape(state["last_error_class"] or "-")
        quarantine_until_escaped = html.escape(state["quarantine_until"] or "-")

        fail_style = 'color: var(--accent-red); font-weight: bold;' if consecutive_failures > 0 else ''

        html_template += f"""
                        <tr>
                            <td><code>{s.id}</code></td>
                            <td>
                                <div><strong style="color: #818cf8;">{title_escaped}</strong></div>
                                <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">
                                    Feed: <a href="{xml_url_escaped}" target="_blank" style="color: #6366f1;">XML</a> 
                                    {f'| 網頁: <a href="{html_url_escaped}" target="_blank" style="color: #6366f1;">Link</a>' if s.html_url else ''}
                                </div>
                            </td>
                            <td><span class="badge badge-info">{cat_name_escaped}</span></td>
                            <td><span class="badge {badge_class_escaped}">{health_escaped}</span></td>
                            <td style="{fail_style}">{consecutive_failures}</td>
                            <td>{last_success_escaped}</td>
                            <td><code style="color: var(--accent-amber);">{last_error_class_escaped}</code></td>
                            <td>{quarantine_until_escaped}</td>
                        </tr>
        """

    html_template += """
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 3: RUNS -->
        <div id="runs-tab" class="tab-content">
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 80px;">批次 ID</th>
                            <th>執行狀態</th>
                            <th>觸發類型</th>
                            <th>執行範圍</th>
                            <th>開始時間</th>
                            <th>結束時間</th>
                            <th>應抓取數</th>
                            <th>已嘗試數</th>
                            <th>成功數</th>
                            <th>失敗數</th>
                            <th>錯誤摘要</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    # Add recent runs to HTML
    if not recent_runs:
        html_template += '<tr><td colspan="11" class="no-data">資料庫中目前沒有任何抓取批次紀錄。</td></tr>'
    else:
        for run in recent_runs:
            status_val = run["run_status"]
            if status_val == "success":
                badge_class = "badge-success"
            elif status_val == "partial_failure":
                badge_class = "badge-warning"
            else:
                badge_class = "badge-error"

            run_status_escaped = html.escape(status_val)
            trigger_type_escaped = html.escape(run["trigger_type"])
            run_scope_escaped = html.escape(run["run_scope"])
            started_at_escaped = html.escape(run["started_at"])
            ended_at_escaped = html.escape(run["ended_at"] or "執行中/中斷")
            badge_class_escaped = html.escape(badge_class)
            error_summary_escaped = html.escape(run["error_summary"] or "-")

            html_template += f"""
                            <tr>
                                <td><code>#{run["fetch_run_id"]}</code></td>
                                <td><span class="badge {badge_class_escaped}">{run_status_escaped}</span></td>
                                <td><span class="badge badge-info">{trigger_type_escaped}</span></td>
                                <td><code>{run_scope_escaped}</code></td>
                                <td>{started_at_escaped}</td>
                                <td>{ended_at_escaped}</td>
                                <td>{run["due_source_count"]}</td>
                                <td>{run["attempted_source_count"]}</td>
                                <td>{run["succeeded_source_count"]}</td>
                                <td>{run["failed_source_count"]}</td>
                                <td style="max-width: 250px; font-family: monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{error_summary_escaped}">
                                    {error_summary_escaped}
                                </td>
                            </tr>
            """

    html_template += """
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        function switchTab(btnElement, tabId) {
            // Hide all tabs
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            
            // Show active tab
            document.getElementById(tabId).classList.add('active');
            
            // Set active button
            btnElement.classList.add('active');
        }

        function filterArticles() {
            const searchVal = document.getElementById('search-box').value.toLowerCase();
            const sourceVal = document.getElementById('source-filter').value;
            const categoryVal = document.getElementById('category-filter').value;
            
            const cards = document.querySelectorAll('.item-card');
            let visibleCount = 0;
            
            cards.forEach(card => {
                const title = card.getAttribute('data-title');
                const summary = card.getAttribute('data-summary');
                const sId = card.getAttribute('data-source-id');
                const cId = card.getAttribute('data-category-id');
                
                const matchesSearch = !searchVal || title.includes(searchVal) || summary.includes(searchVal);
                const matchesSource = !sourceVal || sId === sourceVal;
                const matchesCategory = !categoryVal || cId === categoryVal;
                
                if (matchesSearch && matchesSource && matchesCategory) {
                    card.style.display = 'block';
                    visibleCount++;
                } else {
                    card.style.display = 'none';
                }
            });
            
            document.getElementById('search-result-count').innerText = 
                `已篩選出 ${visibleCount} 筆文章 (共 ${cards.length} 筆)`;
        }

        // Initialize count on load
        window.addEventListener('DOMContentLoaded', () => {
            filterArticles();
        });
    </script>
</body>
</html>
"""

    # 5. Save HTML to output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_template)
    print(f"Ingest Inspector Report generated successfully at: {out_path}")

def get_utc_now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
