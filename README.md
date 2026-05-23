# 医学机器学习自动整理仓库

本仓库用于每天自动收集、整理与医学研究实际应用相关的机器学习资料，包括：

- GitHub 上近期更新且有复现价值的医学机器学习项目
- PubMed 与 Crossref 中的机器学习、深度学习、人工智能医学应用论文候选
- 高影响医学、医学影像、数字医学、生物信息方向期刊中的候选文章
- 可转化到医学科研选题、数据挖掘、临床预测、影像组学、多组学、生物信息学的应用线索

重要说明：中科院分区、影响因子、JCR Quartile 和期刊目录会随年份变动，且权威数据通常需要订阅或人工核验。本流程会把相关论文标记为“高影响/Q1 候选，待按最新版目录复核”，不会把无法实时核验的分区信息当成确定事实。

## 文件结构

- `scripts/collect_ml_med.py`：主采集脚本，使用公开 API 生成日报、原始 JSON 和运行日志。
- `scripts/run_daily.ps1`：每天由计划任务调用的入口脚本。
- `scripts/register_startup_task.ps1`：注册 Windows 登录后自动运行的计划任务。
- `config/topics.json`：关键词、GitHub 检索式、优先期刊和研究方向配置。
- `docs/daily/`：每日 Markdown 整理报告。
- `data/raw/`：每日原始采集 JSON。
- `logs/`：运行日志。

## 手动运行

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daily.ps1
```

如果需要使用更高的 GitHub API 额度，可在用户环境变量中设置 `GITHUB_TOKEN`。如果使用 NCBI API，建议设置 `NCBI_EMAIL`。

## 自动提交与推送

脚本会自动：

1. 初始化本地 Git 仓库。
2. 写入当日 Markdown 报告与 JSON 数据。
3. 提交变更。
4. 尝试推送到 `https://github.com/eMcreC1z/机器学习.git`。

当前目标仓库需要已经存在，并且本机 Git 凭据需要具备推送权限；否则报告仍会保存在本地，推送失败原因会写入日志。

