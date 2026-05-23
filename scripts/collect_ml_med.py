from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "topics.json"
DEFAULT_REMOTE_URL = "https://github.com/eMcreC1z/machine-learning.git"
USER_AGENT = "medical-ml-auto-collector/1.0 (research digest; contact via repo owner)"
ML_TERMS = [
    "machine learning",
    "deep learning",
    "artificial intelligence",
    "foundation model",
    "large language model",
    "llm",
    "neural network",
    "radiomics",
    "computer vision",
    "nlp",
]
MEDICAL_TERMS = [
    "medical",
    "medicine",
    "clinical",
    "patient",
    "diagnosis",
    "prognosis",
    "radiology",
    "imaging",
    "pathology",
    "genomics",
    "omics",
    "ehr",
    "electronic health record",
    "hospital",
    "disease",
    "cancer",
    "survival",
    "biomarker",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def local_today() -> dt.date:
    return dt.datetime.now().date()


def read_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs() -> None:
    for rel in ["docs/daily", "data/raw", "logs"]:
        (REPO_ROOT / rel).mkdir(parents=True, exist_ok=True)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def table_cell(value: Any, limit: int = 120) -> str:
    text = normalize_text(value).replace("|", "\\|")
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def keyword_match(text: str, keyword: str) -> bool:
    text_l = text.lower()
    keyword_l = keyword.lower()
    if len(keyword_l) <= 3 or keyword_l.isupper():
        return re.search(rf"\b{re.escape(keyword_l)}\b", text_l) is not None
    if re.match(r"^[a-z0-9 ]+$", keyword_l):
        return re.search(rf"\b{re.escape(keyword_l)}\b", text_l) is not None
    return keyword_l in text_l


def keyword_count(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if keyword_match(text, keyword))


def is_medical_ml_relevant(text: str) -> bool:
    return keyword_count(text, ML_TERMS) >= 1 and keyword_count(text, MEDICAL_TERMS) >= 1


def request_json(url: str, source: str, token: str | None = None, timeout: int = 15) -> tuple[Any | None, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    started = time.time()
    log = {"source": source, "url": url, "ok": False, "status": None, "error": None, "seconds": None}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            log["status"] = getattr(resp, "status", None)
        data = json.loads(raw.decode("utf-8"))
        log["ok"] = True
        return data, log
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        log["status"] = exc.code
        log["error"] = f"HTTP {exc.code}: {body}"
        return None, log
    except Exception as exc:
        log["error"] = repr(exc)
        return None, log
    finally:
        log["seconds"] = round(time.time() - started, 2)


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def infer_area(text: str, config: dict[str, Any]) -> str:
    for area, keywords in config.get("application_areas", {}).items():
        for keyword in keywords:
            if keyword_match(text, keyword):
                return area
    return "综合医学机器学习"


def github_score(item: dict[str, Any], config: dict[str, Any]) -> float:
    text = " ".join(
        [
            normalize_text(item.get("name")),
            normalize_text(item.get("description")),
            " ".join(item.get("topics") or []),
        ]
    ).lower()
    stars = int(item.get("stargazers_count") or 0)
    forks = int(item.get("forks_count") or 0)
    updated = parse_datetime(item.get("updated_at"))
    days = 90
    if updated:
        days = max(0, (utc_now() - updated.astimezone(dt.timezone.utc)).days)
    keyword_hits = 0
    for keywords in config.get("application_areas", {}).values():
        keyword_hits += sum(1 for keyword in keywords if keyword_match(text, keyword))
    return round(math.log10(stars + 1) * 4 + math.log10(forks + 1) * 1.5 + max(0, 5 - days / 7) + keyword_hits * 0.8, 2)


def fetch_readme_sample(full_name: str, token: str | None, logs: list[dict[str, Any]]) -> str:
    encoded = urllib.parse.quote(full_name, safe="/")
    url = f"https://api.github.com/repos/{encoded}/readme"
    data, log = request_json(url, "github_readme", token=token, timeout=8)
    log["repo"] = full_name
    logs.append(log)
    if not data:
        return ""
    content = data.get("content") or ""
    if data.get("encoding") == "base64":
        try:
            decoded = base64.b64decode(content, validate=False).decode("utf-8", errors="replace")
            return normalize_text(decoded)[:12000]
        except Exception as exc:
            log["ok"] = False
            log["error"] = f"README decode failed: {exc!r}"
            return ""
    return normalize_text(content)[:12000]


def collect_github(config: dict[str, Any], since: dt.date, max_items: int, readme_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    token = os.environ.get("GITHUB_TOKEN")
    logs: list[dict[str, Any]] = []
    candidates: dict[str, dict[str, Any]] = {}
    for query_template in config.get("github_queries", []):
        query = query_template.format(since=since.isoformat())
        params = urllib.parse.urlencode(
            {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": min(max_items, 20),
            }
        )
        url = f"https://api.github.com/search/repositories?{params}"
        data, log = request_json(url, "github", token=token)
        log["query"] = query
        logs.append(log)
        if not data:
            continue
        for item in data.get("items", []):
            full_name = item.get("full_name")
            if not full_name:
                continue
            text = " ".join(
                [
                    normalize_text(item.get("name")),
                    normalize_text(item.get("description")),
                    " ".join(item.get("topics") or []),
                ]
            )
            entry = {
                "full_name": full_name,
                "name": item.get("name"),
                "url": item.get("html_url"),
                "description": normalize_text(item.get("description")),
                "language": item.get("language") or "",
                "stars": item.get("stargazers_count") or 0,
                "forks": item.get("forks_count") or 0,
                "open_issues": item.get("open_issues_count") or 0,
                "updated_at": item.get("updated_at"),
                "pushed_at": item.get("pushed_at"),
                "topics": item.get("topics") or [],
                "application_area": infer_area(text, config),
                "score": github_score(item, config),
            }
            if full_name not in candidates or entry["score"] > candidates[full_name]["score"]:
                candidates[full_name] = entry
        time.sleep(1.1)
    refined: list[dict[str, Any]] = []
    presorted = sorted(candidates.values(), key=lambda row: (row["stars"], row["score"]), reverse=True)
    for entry in presorted[: max(0, readme_limit)]:
        metadata_text = " ".join(
            [
                normalize_text(entry.get("full_name")),
                normalize_text(entry.get("description")),
                " ".join(entry.get("topics") or []),
            ]
        )
        readme = fetch_readme_sample(entry["full_name"], token, logs)
        combined = f"{metadata_text} {readme}"
        if not is_medical_ml_relevant(combined):
            continue
        entry["application_area"] = infer_area(combined, config)
        entry["score"] = round(entry["score"] + keyword_count(combined, ML_TERMS) + keyword_count(combined, MEDICAL_TERMS) * 0.8, 2)
        entry["evidence_terms"] = {
            "ml": [term for term in ML_TERMS if keyword_match(combined, term)][:6],
            "medical": [term for term in MEDICAL_TERMS if keyword_match(combined, term)][:6],
        }
        refined.append(entry)
        if len(refined) >= max_items:
            break
        time.sleep(0.2)
    if len(refined) < max_items:
        for entry in presorted:
            if entry in refined:
                continue
            metadata_text = " ".join(
                [
                    normalize_text(entry.get("full_name")),
                    normalize_text(entry.get("description")),
                    " ".join(entry.get("topics") or []),
                ]
            )
            if not is_medical_ml_relevant(metadata_text):
                continue
            entry["score"] = round(entry["score"] + 1.0, 2)
            refined.append(entry)
            if len(refined) >= max_items:
                break
    repos = sorted(refined, key=lambda row: row["score"], reverse=True)
    return repos[:max_items], logs


def extract_pubmed_ids(record: dict[str, Any]) -> tuple[str | None, str | None]:
    pmid = str(record.get("uid") or "") or None
    doi = None
    for item in record.get("articleids", []) or []:
        if item.get("idtype") == "doi":
            doi = item.get("value")
            break
    if not doi:
        match = re.search(r"10\.\S+", normalize_text(record.get("elocationid")))
        if match:
            doi = match.group(0).rstrip(".")
    return pmid, doi


def journal_priority(journal: str, config: dict[str, Any]) -> bool:
    lowered = normalize_text(journal).lower()
    return any(normalize_text(name).lower() in lowered for name in config.get("priority_journals", []))


def paper_score(paper: dict[str, Any], config: dict[str, Any]) -> float:
    text = f"{paper.get('title', '')} {paper.get('journal', '')} {paper.get('abstract', '')}".lower()
    score = 0.0
    if paper.get("priority_journal"):
        score += 5
    for keywords in config.get("application_areas", {}).values():
        score += sum(0.7 for keyword in keywords if keyword_match(text, keyword))
    if paper.get("doi"):
        score += 0.5
    if paper.get("pmid"):
        score += 0.5
    citations = paper.get("citation_count") or 0
    score += min(3, math.log10(citations + 1))
    return round(score, 2)


def collect_pubmed(config: dict[str, Any], since: dt.date, today: dt.date, max_items: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    logs: list[dict[str, Any]] = []
    email = os.environ.get("NCBI_EMAIL", "")
    params = {
        "db": "pubmed",
        "term": config.get("pubmed_query", ""),
        "retmode": "json",
        "retmax": str(max_items),
        "sort": "pub+date",
        "datetype": "pdat",
        "mindate": since.strftime("%Y/%m/%d"),
        "maxdate": today.strftime("%Y/%m/%d"),
    }
    if email:
        params["email"] = email
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)
    data, log = request_json(url, "pubmed_esearch")
    logs.append(log)
    if not data:
        return [], logs
    ids = data.get("esearchresult", {}).get("idlist", [])[:max_items]
    if not ids:
        return [], logs
    summary_params = {
        "db": "pubmed",
        "retmode": "json",
        "id": ",".join(ids),
    }
    if email:
        summary_params["email"] = email
    summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(summary_params)
    summary, summary_log = request_json(summary_url, "pubmed_esummary")
    logs.append(summary_log)
    if not summary:
        return [], logs
    result = summary.get("result", {})
    papers: list[dict[str, Any]] = []
    for pmid in result.get("uids", []):
        record = result.get(pmid) or {}
        record_pmid, doi = extract_pubmed_ids(record)
        journal = normalize_text(record.get("fulljournalname") or record.get("source"))
        title = normalize_text(record.get("title"))
        paper = {
            "title": title,
            "journal": journal,
            "date": normalize_text(record.get("pubdate")),
            "authors": [normalize_text(a.get("name")) for a in record.get("authors", [])[:6]],
            "pmid": record_pmid,
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{record_pmid}/" if record_pmid else "",
            "source": "PubMed",
            "application_area": infer_area(f"{title} {journal}", config),
            "priority_journal": journal_priority(journal, config),
            "cas_q1_note": "高影响/Q1候选，需按最新版中科院分区、JCR和期刊目录复核" if journal_priority(journal, config) else "需按最新版中科院分区、JCR和期刊目录复核",
        }
        paper["score"] = paper_score(paper, config)
        papers.append(paper)
    return sorted(papers, key=lambda row: row["score"], reverse=True), logs


def crossref_date(item: dict[str, Any]) -> str:
    for key in ["published-online", "published-print", "published", "created"]:
        value = item.get(key)
        parts = value.get("date-parts") if isinstance(value, dict) else None
        if parts and parts[0]:
            return "-".join(str(part).zfill(2) for part in parts[0])
    return ""


def collect_crossref(config: dict[str, Any], since: dt.date, today: dt.date, max_items: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    params = {
        "query.title": config.get("crossref_query", ""),
        "filter": f"from-pub-date:{since.isoformat()},until-pub-date:{today.isoformat()},type:journal-article",
        "rows": str(min(max_items, 50)),
        "sort": "published",
        "order": "desc",
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data, log = request_json(url, "crossref")
    logs = [log]
    if not data:
        return [], logs
    papers: list[dict[str, Any]] = []
    for item in data.get("message", {}).get("items", [])[:max_items]:
        title = normalize_text((item.get("title") or [""])[0])
        journal = normalize_text((item.get("container-title") or [""])[0])
        if not is_medical_ml_relevant(f"{title} {journal}"):
            continue
        doi = normalize_text(item.get("DOI"))
        paper = {
            "title": title,
            "journal": journal,
            "date": crossref_date(item),
            "authors": [],
            "pmid": None,
            "doi": doi,
            "url": item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            "source": "Crossref",
            "application_area": infer_area(f"{title} {journal}", config),
            "priority_journal": journal_priority(journal, config),
            "citation_count": item.get("is-referenced-by-count") or 0,
            "cas_q1_note": "高影响/Q1候选，需按最新版中科院分区、JCR和期刊目录复核" if journal_priority(journal, config) else "需按最新版中科院分区、JCR和期刊目录复核",
        }
        paper["score"] = paper_score(paper, config)
        papers.append(paper)
    return sorted(papers, key=lambda row: row["score"], reverse=True), logs


def merge_papers(*groups: list[dict[str, Any]], max_items: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for group in groups:
        for paper in group:
            key = (paper.get("doi") or paper.get("title") or "").lower()
            key = re.sub(r"\W+", "", key)
            if not key:
                continue
            if key not in seen:
                seen[key] = paper
            else:
                existing = seen[key]
                sources = sorted(set(str(existing.get("source", "")).split("+") + str(paper.get("source", "")).split("+")))
                existing["source"] = "+".join([s for s in sources if s])
                existing["score"] = max(existing.get("score", 0), paper.get("score", 0)) + 0.5
                if not existing.get("pmid") and paper.get("pmid"):
                    existing["pmid"] = paper["pmid"]
                if not existing.get("url") and paper.get("url"):
                    existing["url"] = paper["url"]
    merged = list(seen.values())
    for paper in merged:
        paper["application_area"] = infer_area(f"{paper.get('title', '')} {paper.get('journal', '')}", config)
        paper["score"] = paper_score(paper, config)
    return sorted(merged, key=lambda row: row["score"], reverse=True)[:max_items]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def markdown_report(day: dt.date, repos: list[dict[str, Any]], papers: list[dict[str, Any]], logs: list[dict[str, Any]], config: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 医学机器学习自动整理日报 - {day.isoformat()}")
    lines.append("")
    lines.append(f"> 自动采集时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。数据源包括 GitHub Search、PubMed E-utilities、Crossref。中科院分区、JCR Quartile、影响因子和期刊目录需按最新版官方目录人工复核。")
    lines.append("")
    lines.append("## 今日概览")
    lines.append("")
    lines.append(f"- GitHub 项目候选：{len(repos)} 个")
    lines.append(f"- 论文候选：{len(papers)} 篇")
    lines.append(f"- 高影响/Q1候选论文：{sum(1 for paper in papers if paper.get('priority_journal'))} 篇")
    failed = [log for log in logs if not log.get("ok")]
    lines.append(f"- 采集异常来源：{len(failed)} 个")
    lines.append("")
    if failed:
        lines.append("## 采集限制")
        lines.append("")
        for log in failed:
            lines.append(f"- {log.get('source')}: {table_cell(log.get('error'), 180)}")
        lines.append("")
    lines.append("## 优先关注 GitHub 项目")
    lines.append("")
    lines.append("| 排名 | 项目 | Stars | 最近更新 | 方向 | 推荐理由 |")
    lines.append("| --- | --- | ---: | --- | --- | --- |")
    for idx, repo in enumerate(repos[:15], start=1):
        reason = f"{repo.get('description') or '近期活跃项目'}；评分 {repo.get('score')}"
        link = f"[{table_cell(repo.get('full_name'), 60)}]({repo.get('url')})"
        lines.append(
            f"| {idx} | {link} | {repo.get('stars', 0)} | {table_cell(repo.get('updated_at'), 20)} | {table_cell(repo.get('application_area'), 20)} | {table_cell(reason, 160)} |"
        )
    if not repos:
        lines.append("| - | 未采集到项目 | - | - | - | 查看日志并扩大关键词或配置 GITHUB_TOKEN |")
    lines.append("")
    lines.append("## 高水平论文候选")
    lines.append("")
    lines.append("| 排名 | 题名 | 期刊 | 日期 | DOI/PMID | 方向 | 复核说明 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for idx, paper in enumerate(papers[:20], start=1):
        id_bits = []
        if paper.get("doi"):
            id_bits.append(f"DOI: {paper['doi']}")
        if paper.get("pmid"):
            id_bits.append(f"PMID: {paper['pmid']}")
        title = table_cell(paper.get("title"), 140)
        if paper.get("url"):
            title = f"[{title}]({paper.get('url')})"
        lines.append(
            f"| {idx} | {title} | {table_cell(paper.get('journal'), 55)} | {table_cell(paper.get('date'), 20)} | {table_cell('; '.join(id_bits), 80)} | {table_cell(paper.get('application_area'), 20)} | {table_cell(paper.get('cas_q1_note'), 90)} |"
        )
    if not papers:
        lines.append("| - | 未采集到论文候选 | - | - | - | - | 查看日志并扩大关键词 |")
    lines.append("")
    lines.append("## 医学研究应用整理")
    lines.append("")
    area_to_repos: dict[str, list[dict[str, Any]]] = {}
    area_to_papers: dict[str, list[dict[str, Any]]] = {}
    for repo in repos:
        area_to_repos.setdefault(repo.get("application_area", "综合医学机器学习"), []).append(repo)
    for paper in papers:
        area_to_papers.setdefault(paper.get("application_area", "综合医学机器学习"), []).append(paper)
    all_areas = sorted(set(area_to_repos) | set(area_to_papers))
    for area in all_areas:
        top_repo = area_to_repos.get(area, [{}])[0]
        top_paper = area_to_papers.get(area, [{}])[0]
        lines.append(f"### {area}")
        lines.append("")
        if top_repo.get("full_name"):
            lines.append(f"- 可复用项目：[{top_repo['full_name']}]({top_repo.get('url')})，适合先看 README、数据要求、许可证和最近提交。")
        if top_paper.get("title"):
            lines.append(f"- 论文线索：{top_paper.get('title')}。优先核验研究设计、样本来源、外部验证和代码可得性。")
        lines.append("- 转化建议：把候选方法拆成数据来源、标签定义、模型、验证、统计报告和临床解释六个模块，先做小样本复现实验，再决定是否扩展到真实课题。")
        lines.append("")
    lines.append("## 质量控制清单")
    lines.append("")
    for flag in config.get("quality_flags", []):
        lines.append(f"- {flag}")
    lines.append("")
    lines.append("## 下一步人工复核")
    lines.append("")
    lines.append("- 对高影响/Q1候选论文，按最新版中科院分区、JCR、期刊官网和单位可访问数据库复核。")
    lines.append("- 对 GitHub 项目，优先检查许可证、数据访问权限、最近维护状态、依赖可安装性和是否包含医学数据合规说明。")
    lines.append("- 对可能进入课题的论文，补充全文级精读卡：PICO、数据来源、纳排标准、结局、统计方法、模型验证、局限性和可复现性。")
    lines.append("")
    lines.append("## 运行来源")
    lines.append("")
    for log in logs:
        status = "OK" if log.get("ok") else "FAILED"
        lines.append(f"- {log.get('source')}: {status}; status={log.get('status')}; seconds={log.get('seconds')}")
    lines.append("")
    lines.append(f"本次运行已完成：{day.isoformat()}_ml_med_research_digest.md")
    lines.append("")
    return "\n".join(lines)


def git_run(args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def ensure_git(remote_url: str) -> list[str]:
    messages: list[str] = []
    if not (REPO_ROOT / ".git").exists():
        git_run(["init"], check=True)
        git_run(["checkout", "-B", "main"], check=True)
        messages.append("Initialized git repository.")
    branch = git_run(["branch", "--show-current"])
    if not branch.stdout.strip():
        git_run(["checkout", "-B", "main"], check=True)
    remote = git_run(["remote", "get-url", "origin"])
    if remote.returncode != 0:
        git_run(["remote", "add", "origin", remote_url], check=True)
        messages.append(f"Added origin remote: {remote_url}")
    elif remote.stdout.strip() != remote_url:
        messages.append(f"Origin remote differs and was left unchanged: {remote.stdout.strip()}")
    name = git_run(["config", "user.name"])
    email = git_run(["config", "user.email"])
    if not name.stdout.strip():
        git_run(["config", "user.name", "medical-ml-auto"])
        messages.append("Set local git user.name to medical-ml-auto.")
    if not email.stdout.strip():
        git_run(["config", "user.email", "medical-ml-auto@example.local"])
        messages.append("Set local git user.email to medical-ml-auto@example.local.")
    return messages


def commit_and_push(day: dt.date, remote_url: str, push: bool) -> list[str]:
    messages = ensure_git(remote_url)
    git_run(["add", "-A"], check=True)
    staged = git_run(["diff", "--cached", "--quiet"])
    if staged.returncode == 0:
        messages.append("No changes to commit.")
        return messages
    commit = git_run(["commit", "-m", f"daily: update medical ML digest {day.isoformat()}"])
    if commit.returncode != 0:
        messages.append(f"Commit failed: {commit.stderr.strip() or commit.stdout.strip()}")
        return messages
    messages.append(commit.stdout.strip())
    if push:
        push_result = git_run(["push", "-u", "origin", "main"])
        if push_result.returncode == 0:
            messages.append(push_result.stdout.strip() or "Pushed to origin/main.")
        else:
            messages.append(f"Push failed: {push_result.stderr.strip() or push_result.stdout.strip()}")
    return messages


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect medical machine learning resources and write a daily digest.")
    parser.add_argument("--days-back", type=int, default=10, help="Lookback window for fresh sources.")
    parser.add_argument("--max-github", type=int, default=30, help="Maximum GitHub repositories to keep.")
    parser.add_argument("--max-papers", type=int, default=40, help="Maximum papers to keep after merging.")
    parser.add_argument("--github-readme-limit", type=int, default=18, help="Maximum GitHub README files to fetch for relevance checks.")
    parser.add_argument("--commit", action="store_true", help="Commit generated files to git.")
    parser.add_argument("--push", action="store_true", help="Push committed changes to origin/main.")
    parser.add_argument("--remote-url", default=os.environ.get("ML_MED_REMOTE_URL", DEFAULT_REMOTE_URL), help="Git remote URL.")
    parser.add_argument("--date", default="", help="Override run date, YYYY-MM-DD.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    ensure_dirs()
    config = read_config()
    day = dt.date.fromisoformat(args.date) if args.date else local_today()
    since = day - dt.timedelta(days=max(1, args.days_back))
    logs: list[dict[str, Any]] = []

    repos, github_logs = collect_github(config, since, args.max_github, args.github_readme_limit)
    logs.extend(github_logs)

    pubmed, pubmed_logs = collect_pubmed(config, since, day, args.max_papers)
    logs.extend(pubmed_logs)

    crossref, crossref_logs = collect_crossref(config, since, day, args.max_papers)
    logs.extend(crossref_logs)

    papers = merge_papers(pubmed, crossref, max_items=args.max_papers, config=config)

    raw_prefix = REPO_ROOT / "data" / "raw" / day.isoformat()
    write_json(raw_prefix.with_name(f"{day.isoformat()}_github_repos.json"), repos)
    write_json(raw_prefix.with_name(f"{day.isoformat()}_papers.json"), papers)
    write_json(raw_prefix.with_name(f"{day.isoformat()}_run_log.json"), logs)

    report = markdown_report(day, repos, papers, logs, config)
    report_path = REPO_ROOT / "docs" / "daily" / f"{day.isoformat()}_ml_med_research_digest.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    git_messages: list[str] = []
    if args.commit:
        git_messages = commit_and_push(day, args.remote_url, push=args.push)
        write_json(REPO_ROOT / "logs" / f"{day.isoformat()}_git_result.json", git_messages)

    print(f"Wrote report: {report_path}")
    print(f"GitHub repos: {len(repos)}; papers: {len(papers)}; failed requests: {sum(1 for log in logs if not log.get('ok'))}")
    for message in git_messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
