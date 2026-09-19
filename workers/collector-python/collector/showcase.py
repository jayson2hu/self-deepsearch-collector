from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

from collector.storage import connect


def build_showcase(database: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir / "media"
    media_dir.mkdir(exist_ok=True)
    connection = connect(database)
    try:
        works = [dict(row) for row in connection.execute(
            "SELECT work_id, external_id, canonical_code, title, release_date, studio_name, rights_status, publication_status, checked_at FROM works ORDER BY release_date DESC, canonical_code"
        )]
        aliases: dict[str, list[str]] = {}
        for row in connection.execute("SELECT work_id, alias FROM performer_aliases ORDER BY alias"):
            aliases.setdefault(row["work_id"], []).append(row["alias"])
        downloaded_media: dict[str, list[dict[str, object]]] = {}
        for row in connection.execute(
            """
            SELECT work_id, media_candidate_id, purpose, display_position, local_path, sha256,
              mime_type, width, height, byte_size, rights_status, review_status,
              source_page_url, connector_version
            FROM media_candidates
            WHERE display_eligible=1 AND download_status='downloaded'
            ORDER BY work_id, display_position
            """
        ):
            downloaded_media.setdefault(row["work_id"], []).append(dict(row))
        pipeline_counts = {
            "runs": int(connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]),
            "media_candidates": int(connection.execute("SELECT COUNT(*) FROM media_candidates").fetchone()[0]),
            "display_eligible_media": int(connection.execute("SELECT COUNT(*) FROM media_candidates WHERE display_eligible=1").fetchone()[0]),
            "errors": int(connection.execute("SELECT COUNT(*) FROM collection_errors").fetchone()[0]),
        }
    finally:
        connection.close()

    complete = 0
    for work in works:
        work["performer_aliases"] = aliases.get(work["work_id"], [])
        work["cover"] = None
        work["gallery"] = []
        for media_item in downloaded_media.get(work["work_id"], []):
            if not media_item["local_path"]:
                continue
            source = Path(str(media_item["local_path"]))
            suffix = source.suffix.lower()
            target = media_dir / f"{media_item['media_candidate_id']}{suffix}"
            shutil.copyfile(source, target)
            rendered_media = {**media_item, "url": f"media/{target.name}"}
            if media_item["purpose"] == "cover":
                work["cover"] = rendered_media
            else:
                work["gallery"].append(rendered_media)
        required = [work["canonical_code"], work["title"], work["release_date"], work["studio_name"], work["performer_aliases"]]
        work["field_complete"] = all(value not in (None, "", []) for value in required)
        complete += int(work["field_complete"])

    dataset = {
        "real_data": True,
        "publication_mode": "internal_acceptance_only",
        "notice": "真实公开页面元数据与私有 staging 封面；权利审核完成前不得作为正式公开图片发布。",
        "counts": {
            "works": len(works),
            "field_complete": complete,
            "covers_downloaded": sum(work["cover"] is not None for work in works),
            "images_downloaded": sum((1 if work["cover"] else 0) + len(work["gallery"]) for work in works),
            **pipeline_counts,
        },
        "works": works,
    }
    (output_dir / "catalog.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards = []
    for work in works:
        image = f'<img loading="eager" alt="{html.escape(work["canonical_code"])} 封面" src="{html.escape(work["cover"]["url"])}">' if work["cover"] else '<div class="placeholder">无封面</div>'
        aliases_text = "、".join(work["performer_aliases"]) or "未知"
        cover_meta = work["cover"] or {}
        media_text = "无本地图片"
        if cover_meta:
            media_text = f'{cover_meta["mime_type"]} · {cover_meta["width"]}×{cover_meta["height"]} · 详情图 {len(work["gallery"])}'
        search_text = " ".join(filter(None, [work["canonical_code"], work["title"], work["studio_name"], aliases_text])).lower()
        complete_label = "字段完整" if work["field_complete"] else "字段待补"
        cards.append(f'''<article class="card" data-search="{html.escape(search_text, quote=True)}" data-complete="{"complete" if work["field_complete"] else "incomplete"}">
<button class="cover-button" type="button" data-work-id="{html.escape(work["work_id"], quote=True)}" aria-label="查看 {html.escape(work["canonical_code"], quote=True)} 数据详情">{image}<span>查看数据详情</span></button>
<div class="body"><div class="eyebrow"><strong>{html.escape(work["canonical_code"])}</strong><span class="quality {"ok" if work["field_complete"] else "warn"}">{complete_label}</span></div><h2>{html.escape(work["title"])}</h2>
<dl><div><dt>发行日期</dt><dd>{html.escape(work["release_date"] or "未知")}</dd></div><div><dt>片商</dt><dd>{html.escape(work["studio_name"] or "未知")}</dd></div><div><dt>演员</dt><dd>{html.escape(aliases_text)}</dd></div></dl>
<div class="media-line"><span>{html.escape(media_text)}</span><span>权利待审核</span></div></div></article>''')
    catalog_js = json.dumps(dataset, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    page = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>self-deepsearch 真实数据预览</title>
<style>
:root{--ink:#191814;--muted:#756f64;--paper:#f4f1ea;--card:#fffdfa;--line:#ded8cc;--accent:#d75026;--accent-soft:#ffe7db;--green:#2f6a4f;--green-soft:#e6f3eb;--shadow:0 18px 50px rgba(39,31,22,.1)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,"Microsoft YaHei",sans-serif}.hero{padding:24px max(22px,5vw) 42px;background:#1d1c18;color:#fff}.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:48px}.brand{font-size:13px;letter-spacing:.18em;font-weight:800}.environment{border:1px solid #4c4941;border-radius:999px;padding:7px 11px;color:#d8d3ca;font-size:12px}.hero-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);gap:36px;align-items:end}.kicker{color:#ff8d64;font-size:12px;letter-spacing:.16em;font-weight:800}.hero h1{font-size:clamp(34px,5vw,66px);line-height:1.03;letter-spacing:-.04em;margin:12px 0 18px}.hero p{max-width:760px;color:#c9c3b9;line-height:1.75;margin:0}.hero-note{border:1px solid #3f3c35;border-radius:20px;padding:20px;background:#26241f}.hero-note strong{display:block;margin-bottom:8px}.hero-note span{color:#aaa398;font-size:13px;line-height:1.7}.stats{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:10px;padding:0 max(22px,5vw);transform:translateY(-20px)}.stat{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:var(--shadow)}.stat b{display:block;font-size:24px;letter-spacing:-.04em}.stat span{font-size:12px;color:var(--muted)}main{padding:16px max(22px,5vw) 70px}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:18px;margin:20px 0 24px}.search{flex:1;max-width:540px;position:relative}.search input{width:100%;border:1px solid var(--line);background:#fff;border-radius:13px;padding:13px 16px 13px 42px;font:inherit;outline:none}.search input:focus{border-color:#9d9385;box-shadow:0 0 0 3px #e5dfd5}.search:before{content:'⌕';position:absolute;left:15px;top:8px;font-size:22px;color:#766e62}.filters{display:flex;gap:8px;flex-wrap:wrap}.filters button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 13px;color:#5f594f;cursor:pointer}.filters button.active{background:var(--ink);color:#fff;border-color:var(--ink)}.result-count{color:var(--muted);font-size:13px;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:22px}.card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 10px 34px rgba(47,38,27,.06);transition:transform .2s,box-shadow .2s}.card:hover{transform:translateY(-3px);box-shadow:var(--shadow)}.cover-button{display:block;position:relative;width:100%;padding:0;border:0;background:#ddd5ca;cursor:pointer;text-align:left}.cover-button img,.placeholder{display:grid;place-items:center;width:100%;aspect-ratio:3/2;object-fit:cover;background:#ddd5ca}.cover-button>span{position:absolute;right:12px;bottom:12px;background:rgba(24,22,19,.85);color:#fff;border-radius:999px;padding:7px 10px;font-size:11px;opacity:0;transform:translateY(4px);transition:.2s}.cover-button:hover>span,.cover-button:focus-visible>span{opacity:1;transform:none}.body{padding:18px}.eyebrow{display:flex;justify-content:space-between;align-items:center;gap:12px}.eyebrow strong{color:var(--accent);font-size:14px}.quality{white-space:nowrap;border-radius:999px;padding:5px 8px;font-size:11px}.quality.ok{background:var(--green-soft);color:var(--green)}.quality.warn{background:#fff0cf;color:#8a641b}.card h2{font-size:17px;line-height:1.55;margin:11px 0 17px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:79px}.card dl{margin:0}.card dl div{display:grid;grid-template-columns:70px minmax(0,1fr);gap:8px;margin:8px 0;font-size:13px}.card dt{color:var(--muted)}.card dd{margin:0;overflow-wrap:anywhere}.media-line{display:flex;justify-content:space-between;gap:12px;border-top:1px solid #ece7de;margin-top:15px;padding-top:13px;color:#8a8175;font-size:11px}.media-line span:last-child{color:#9b5d28}.empty{display:none;text-align:center;padding:70px 20px;color:var(--muted)}.drawer-backdrop{position:fixed;inset:0;background:rgba(20,18,15,.52);z-index:20;opacity:1;transition:.2s}.drawer-backdrop[hidden]{display:none}.drawer{position:absolute;right:0;top:0;min-height:100%;width:min(680px,100%);background:#f8f5ef;padding:22px;box-shadow:-18px 0 70px #0003;overflow:auto}.drawer-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.drawer-head strong{font-size:13px;letter-spacing:.12em}.close{border:1px solid var(--line);background:#fff;border-radius:50%;width:40px;height:40px;font-size:22px;cursor:pointer}.detail-image{width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:18px;background:#ddd5ca}.detail-thumbs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.detail-thumb{padding:0;border:2px solid transparent;border-radius:10px;overflow:hidden;background:#ddd5ca;cursor:pointer}.detail-thumb.active{border-color:var(--accent)}.detail-thumb img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover}.detail-code{color:var(--accent);font-weight:800;margin-top:22px}.drawer h2{font-size:25px;line-height:1.35;margin:8px 0 22px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.detail-item{border:1px solid var(--line);background:#fff;border-radius:13px;padding:13px}.detail-item.wide{grid-column:1/-1}.detail-item span{display:block;color:var(--muted);font-size:11px;margin-bottom:6px}.detail-item b{font-size:13px;overflow-wrap:anywhere}.evidence{margin-top:18px;padding:18px;background:#211f1b;color:#fff;border-radius:16px}.evidence h3{margin:0 0 14px;font-size:14px}.evidence-row{display:grid;grid-template-columns:110px 1fr;gap:10px;margin:9px 0;font-size:12px}.evidence-row span{color:#aaa398}.evidence-row code{white-space:normal;word-break:break-all;color:#eee8df}.page-footer{padding:25px max(22px,5vw);border-top:1px solid var(--line);color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}@media(max-width:900px){.hero-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}.toolbar{align-items:stretch;flex-direction:column}.search{max-width:none}}@media(max-width:560px){.topbar{margin-bottom:32px}.hero{padding-bottom:34px}.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.detail-grid{grid-template-columns:1fr}.detail-item.wide{grid-column:auto}.media-line{flex-direction:column}.drawer{padding:16px}.detail-thumbs{grid-template-columns:repeat(2,1fr)}}
</style></head><body>
<header class="hero"><div class="topbar"><div class="brand">SELF DEEPSEARCH · COLLECTOR</div><div class="environment">LOCAL / PRIVATE STAGING</div></div><div class="hero-grid"><div><div class="kicker">REAL DATA PREVIEW</div><h1>真实采集数据预览</h1><p>基于真实公开页面解析结果生成。图片已临时保存到本地页面目录，当前仅用于内部数据验收；权利审核完成前不得作为正式公开媒体发布。</p></div><aside class="hero-note"><strong>这不是模拟数据</strong><span>页面从 SQLite 数据基座生成，字段证据、图片 hash、采集批次和失败记录均可追溯；本页不请求任何外部资源。</span></aside></div></header>
<section class="stats" aria-label="数据概览"><div class="stat"><b>__WORKS__</b><span>真实作品</span></div><div class="stat"><b>__COMPLETE__</b><span>字段完整</span></div><div class="stat"><b>__IMAGES__</b><span>本地图片</span></div><div class="stat"><b>__CANDIDATES__</b><span>图片候选</span></div><div class="stat"><b>__ELIGIBLE__</b><span>展示槽位</span></div><div class="stat"><b>__ERRORS__</b><span>明确错误记录</span></div></section>
<main><div class="toolbar"><label class="search"><input id="search" type="search" placeholder="搜索番号、标题、片商或演员" autocomplete="off"></label><div class="filters" aria-label="完整性筛选"><button class="active" data-filter="all" type="button">全部</button><button data-filter="complete" type="button">字段完整</button><button data-filter="incomplete" type="button">字段待补</button></div></div><div id="result-count" class="result-count"></div><section class="grid" id="grid">__CARDS__</section><div class="empty" id="empty">没有符合条件的数据</div></main>
<div class="drawer-backdrop" id="drawer-backdrop" hidden><section class="drawer" role="dialog" aria-modal="true" aria-labelledby="detail-title"><div class="drawer-head"><strong>DATA RECORD / LOCAL MEDIA</strong><button class="close" id="close-drawer" type="button" aria-label="关闭详情">×</button></div><img class="detail-image" id="detail-image" alt=""><div class="detail-thumbs" id="detail-thumbs" aria-label="详情图片"></div><div class="detail-code" id="detail-code"></div><h2 id="detail-title"></h2><div class="detail-grid" id="detail-grid"></div><div class="evidence" id="evidence"></div></section></div>
<footer class="page-footer"><span>临时媒体目录：runtime/real-showcase/media/</span><span>发布状态：staging · rights_status=needs_review</span></footer>
<script>const catalog=__CATALOG__;const cards=[...document.querySelectorAll('.card')];const search=document.querySelector('#search');const count=document.querySelector('#result-count');const empty=document.querySelector('#empty');let filter='all';function apply(){const q=search.value.trim().toLowerCase();let visible=0;for(const card of cards){const matchesText=!q||card.dataset.search.includes(q);const matchesFilter=filter==='all'||card.dataset.complete===filter;const show=matchesText&&matchesFilter;card.hidden=!show;if(show)visible++}count.textContent=`显示 ${visible} / ${cards.length} 条真实数据`;empty.style.display=visible?'none':'block'}search.addEventListener('input',apply);for(const button of document.querySelectorAll('[data-filter]'))button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelector('.filters .active').classList.remove('active');button.classList.add('active');apply()});const backdrop=document.querySelector('#drawer-backdrop');const detailImage=document.querySelector('#detail-image');const detailThumbs=document.querySelector('#detail-thumbs');const detailCode=document.querySelector('#detail-code');const detailTitle=document.querySelector('#detail-title');const detailGrid=document.querySelector('#detail-grid');const evidence=document.querySelector('#evidence');const esc=value=>String(value??'未知');const field=(label,value,wide=false)=>`<div class="detail-item${wide?' wide':''}"><span>${label}</span><b>${esc(value)}</b></div>`;function showMedia(work,media,index){const item=media[index];detailImage.src=item.url;detailImage.alt=`${work.canonical_code} ${item.purpose==='cover'?'封面':`详情图 ${item.display_position}`}`;for(const [thumbIndex,thumb] of [...detailThumbs.children].entries())thumb.classList.toggle('active',thumbIndex===index);evidence.innerHTML=`<h3>采集与图片证据</h3><div class="evidence-row"><span>图片用途</span><code>${item.purpose} / position=${esc(item.display_position)}</code></div><div class="evidence-row"><span>来源对象</span><code>${esc(work.external_id)}</code></div><div class="evidence-row"><span>核验时间</span><code>${esc(work.checked_at)}</code></div><div class="evidence-row"><span>解析器</span><code>${esc(item.connector_version)}</code></div><div class="evidence-row"><span>图片规格</span><code>${esc(item.mime_type)} · ${esc(item.width)}×${esc(item.height)} · ${esc(item.byte_size)} bytes</code></div><div class="evidence-row"><span>SHA-256</span><code>${esc(item.sha256)}</code></div><div class="evidence-row"><span>权利状态</span><code>${esc(item.rights_status)} / ${esc(item.review_status)}</code></div>`}function openDetail(id){const work=catalog.works.find(item=>item.work_id===id);if(!work)return;const media=[work.cover,...work.gallery].filter(Boolean);detailCode.textContent=work.canonical_code;detailTitle.textContent=work.title;detailGrid.innerHTML=field('发行日期',work.release_date)+field('片商',work.studio_name)+field('演员',work.performer_aliases.join('、')||'未知',true)+field('字段状态',work.field_complete?'完整':'待补')+field('本地图片',`${media.length} 张`);detailThumbs.innerHTML=media.map((item,index)=>`<button class="detail-thumb${index===0?' active':''}" type="button" aria-label="查看第 ${index+1} 张图片"><img src="${item.url}" alt=""></button>`).join('');[...detailThumbs.children].forEach((thumb,index)=>thumb.addEventListener('click',()=>showMedia(work,media,index)));showMedia(work,media,0);backdrop.hidden=false;document.body.style.overflow='hidden';document.querySelector('#close-drawer').focus()}for(const button of document.querySelectorAll('.cover-button'))button.addEventListener('click',()=>openDetail(button.dataset.workId));function closeDrawer(){backdrop.hidden=true;document.body.style.overflow=''}document.querySelector('#close-drawer').addEventListener('click',closeDrawer);backdrop.addEventListener('click',event=>{if(event.target===backdrop)closeDrawer()});document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!backdrop.hidden)closeDrawer()});apply();window.__SHOWCASE_READY__=true;</script></body></html>'''
    replacements = {
        "__WORKS__": str(len(works)),
        "__COMPLETE__": f"{complete}/{len(works)}",
        "__IMAGES__": str(dataset["counts"]["images_downloaded"]),
        "__CANDIDATES__": str(dataset["counts"]["media_candidates"]),
        "__ELIGIBLE__": str(dataset["counts"]["display_eligible_media"]),
        "__ERRORS__": str(dataset["counts"]["errors"]),
        "__CARDS__": "".join(cards),
        "__CATALOG__": catalog_js,
    }
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    report = {"output_dir": str(output_dir), **dataset["counts"], "external_requests": 0}
    (output_dir / "showcase-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an offline real-data acceptance showcase")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_showcase(args.database, args.output_dir)
    except (OSError, ValueError) as exc:
        print(json.dumps({"level": "error", "code": "SHOWCASE_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
