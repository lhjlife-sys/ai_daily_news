# RSS AI Digest Email Pipeline

[![Daily RSS AI Digest](https://github.com/lhjlife-sys/ai_daily_news/actions/workflows/daily-news.yml/badge.svg)](https://github.com/lhjlife-sys/ai_daily_news/actions/workflows/daily-news.yml)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-LLM-412991?logo=openai&logoColor=white)
![Resend](https://img.shields.io/badge/Email-Resend-000000)
![GitHub Actions](https://img.shields.io/badge/Automation-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)
![License](https://img.shields.io/badge/License-GPL--3.0-blue)

**语言：[English](README.md) | 简体中文**

一个自动化 RSS 新闻摘要邮件流水线：从配置好的 RSS 源抓取新闻，用 LLM 选择高信号内容，再把入选文章翻译、总结并结构化，渲染成 HTML 邮件，最后通过 Resend 发送到邮箱。

项目可以通过 GitHub Actions 无人值守运行，同时也支持本地 dry-run，方便调试 RSS 源、优化 prompt、调整筛选策略和迭代邮件模板。此公开仓库中签入的 workflow 默认只手动触发并使用 dry-run。

这是去隐私后的 public 版本：真实运行日志、已发送状态、本地输出和私有自动化历史都不会包含在此仓库中。

## 亮点

- **批量 LLM 选择**：从均衡候选池中一次性选择文章，避免逐条评分带来的多次调用。
- **模型分工控成本**：选题阶段使用更强模型，翻译阶段使用更低成本模型。
- **硬性多样性约束**：LLM 选择后继续按来源和主题 cluster 做二次裁剪。
- **LLM 前硬过滤**：在选题前排除明显不符合偏好的体育、娱乐、名人、生活方式、音乐、电影和评论类内容。
- **结构化摘要输出**：生成中文标题、事实摘要、关键要点和重要性说明。
- **可观测运行日志**：记录 prompts、抓取报告、选择指标、输出指标和入选文章。
- **GitHub 原生自动化**：显式开启后，可在成功发送后把 state 和 logs 提交回仓库。

## 目录

- [工作流程](#工作流程)
- [当前默认值](#当前默认值)
- [项目结构](#项目结构)
- [本地安装](#本地安装)
- [环境变量](#环境变量)
- [本地运行](#本地运行)
- [配置说明](#配置说明)
- [选择与多样性控制](#选择与多样性控制)
- [GitHub Actions](#github-actions)
- [输出与日志](#输出与日志)
- [常见问题](#常见问题)
- [License](#license)

## 工作流程

```text
RSS feeds
  -> 抓取并标准化
  -> 基于 state 和近期 logs 去重
  -> 硬过滤排除低优先级内容
  -> 按来源/主题均衡采样候选池
  -> LLM 批量选择
  -> 二次多样性裁剪
  -> LLM 批量翻译与结构化
  -> HTML 渲染
  -> Resend 邮件发送
  -> 可选更新 state 和运行日志
```
<img width="1472" height="1680" alt="rss_ai_email_pipeline_architecture" src="https://github.com/user-attachments/assets/dfe786c2-4c89-4a80-8325-3538e660940b" />


### 核心步骤：

1. 从 `config/sources.yaml` 中配置的 RSS 源抓取文章。
2. 标准化 RSS item，并结合 `state/sent_items.json` 与近期运行日志去重。
3. 在进入 LLM 前执行硬过滤。
4. 按来源和粗粒度主题构建均衡候选池。
5. 调用 LLM 选择最符合用户偏好的高价值文章。
6. 对 LLM 选择结果按来源和主题执行二次硬裁剪。
7. 使用独立的 LLM 翻译/总结阶段，生成结构化中文摘要字段。
8. 使用 `templates/daily_digest.html.j2` 渲染 `out/daily_digest.html`。
9. 通过 Resend 发送邮件。
10. 写入运行日志、输出 artifact，并更新已发送状态。

## 当前默认值

| 配置 | 默认值 |
| --- | --- |
| OpenAI API 模式 | `responses` |
| 选择模型 | `gpt-4.1-mini` |
| 选择推理强度 | 未设置 |
| 翻译模型 | `gpt-4.1-nano` |
| 目标语言 | `zh-CN` |
| 最大候选数 | `50` |
| 最大入选数 | `10` |
| 最低匹配分 | `60` |
| 每个来源最多 | `2` |
| 每个主题 cluster 最多 | `2` |
| 时区 | `Asia/Shanghai` |

## 项目结构

```text
config/
  prompts.yaml       选择与翻译阶段的 LLM prompts
  settings.yaml      用户偏好配置
  sources.yaml       RSS 源列表

scripts/
  news_pipeline.py   主流程入口
  rss_fetcher.py     RSS 抓取与解析
  ai_processor.py    OpenAI JSON 调用与结构化响应模型
  email_renderer.py  HTML 渲染
  email_sender_resend.py
  state_store.py     已发送 item 去重状态
  run_log_store.py   运行日志与日志级去重

templates/
  daily_digest.html.j2

state/
  sent_items.json

logs/
  latest.json
  run-*.json

out/
  daily_digest.html
  processed_items.json
```

## 本地安装

创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 环境变量

正常发送邮件时必需：

| 变量 | 用途 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API 访问 |
| `RESEND_API_KEY` | Resend API 访问 |
| `EMAIL_FROM` | Resend 已验证发件人 |
| `EMAIL_TO` | 摘要接收人 |

可选运行参数：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `TARGET_LANGUAGE` | `zh-CN` | 输出语言 |
| `TIMEZONE` | `Asia/Shanghai` | digest 展示时区 |
| `MAX_CANDIDATES` | `50` | 送入选择阶段的候选数 |
| `MAX_SELECTED` | `10` | 最终 digest 最大文章数 |
| `MIN_MATCH_SCORE` | `60` | LLM 选择最低分 |
| `OPENAI_MODEL` | `gpt-4.1-nano` | 默认/兜底模型值 |
| `OPENAI_API_MODE` | `responses` | OpenAI API 模式：`responses` 或 `chat` |
| `SELECTION_MODEL` | `gpt-4.1-mini` | 选题阶段模型 |
| `SELECTION_REASONING_EFFORT` | 空 | 可选的选题推理强度，适用于支持的推理模型：`none`、`minimal`、`low`、`medium`、`high` 或 `xhigh` |
| `SELECTION_REASONING_SUMMARY` | 空 | 可选的 Responses API 推理摘要，仅写入日志：`off`、`auto`、`concise` 或 `detailed` |
| `TRANSLATION_MODEL` | `gpt-4.1-nano` | 翻译与总结阶段模型 |
| `TRANSLATION_BATCH_SIZE` | `3` | 每次翻译调用处理的文章数；调低可减少批量翻译漏项 |
| `MAX_PER_SOURCE` | `2` | 来源多样性限制 |
| `MAX_PER_TOPIC_CLUSTER` | `2` | 主题多样性限制 |
| `USER_PREFERENCE` | 来自 `config/settings.yaml` | 覆盖用户偏好 |
| `DRY_RUN` | 空 | 设置 `DRY_RUN=1` 跳过邮件与 state 更新 |
| `COMMIT_RUNTIME_STATE` | 公开 workflow 中为 `0` | 设置 `COMMIT_RUNTIME_STATE=1` 允许 GitHub Actions 提交生成的 state/logs |

## 本地运行

Dry-run：

```bash
DRY_RUN=1 python scripts/news_pipeline.py
```

正常运行：

```bash
python scripts/news_pipeline.py
```

生成文件：

- `out/daily_digest.html`
- `out/processed_items.json`
- `logs/latest.json`
- `logs/run-*.json`

正常模式还会更新：

- `state/sent_items.json`

## 配置说明

### RSS 源

编辑 `config/sources.yaml` 添加、删除或重命名 RSS 源：

```yaml
- id: example
  name: "Example Feed"
  url: "https://example.com/rss.xml"
```

### 用户偏好

编辑 `config/settings.yaml`：

```yaml
pipeline:
  user_preference: >
    MUST include: high-signal technology, science, business, and world news.
    PREFER: software engineering, AI research, hardware, security, finance, policy, and practical technical context.
    EXCLUDE: entertainment, sports, celebrity news, lifestyle, music/film reviews.
    When in doubt, prefer factual depth and broad reader relevance.
```

这段文本会传入选择阶段，作为个人编辑偏好。

### Prompts

编辑 `config/prompts.yaml` 可调整：

- `selection.system`
- `selection.user`
- `translation.system`
- `translation.user`

翻译 prompt 使用 `{{ items_json }}` 作为批量输入，并要求模型返回：

- `translated_title`
- `translated_summary`
- `key_points`
- `why_it_matters`

## 选择与多样性控制

pipeline 同时使用 prompt 软约束和 Python 侧硬约束。

进入 LLM 前：

- 用关键词过滤明显不符合偏好的内容。
- 按来源/主题桶均衡采样，减少来源偏斜。

LLM 选择后：

- 丢弃低于 `MIN_MATCH_SCORE` 的文章。
- 执行 `MAX_PER_SOURCE` 限制。
- 执行 `MAX_PER_TOPIC_CLUSTER` 限制。
- 使用轻量标题 token overlap，减少同一事件重复出现。

运行日志会写入 `selection_metrics` 和 `output_metrics`，包括：

- 排除关键词命中数
- 候选来源分布
- 候选主题分布
- 入选来源分布
- 入选主题分布
- 二次裁剪拒绝原因统计
- 摘要句数统计

## GitHub Actions

工作流文件：

```text
.github/workflows/daily-news.yml
```

公开仓库默认只保留手动触发（`workflow_dispatch`），并且在没有 repository variables 覆盖时使用 `DRY_RUN=1`，避免意外公开个人运行状态或日志。

需要配置的 GitHub Actions secrets：

- `OPENAI_API_KEY`
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_TO`

可选运行参数可以配置在 GitHub 仓库的 **Settings -> Secrets and variables -> Actions -> Variables**。GitHub repository variables 不会自动导出到 job；`.github/workflows/daily-news.yml` 会显式把支持的 `vars.*` 映射为环境变量，再运行 `scripts/news_pipeline.py`。

支持的 repository variables 包括：

- `TARGET_LANGUAGE`、`TIMEZONE`、`MAX_CANDIDATES`、`MAX_SELECTED`
- `MIN_MATCH_SCORE`、`MAX_PER_SOURCE`、`MAX_PER_TOPIC_CLUSTER`
- `OPENAI_API_MODE`、`OPENAI_MODEL`
- `SELECTION_MODEL`、`OPENAI_SELECTION_MODEL`
- `SELECTION_REASONING_EFFORT`、`OPENAI_SELECTION_REASONING_EFFORT`
- `SELECTION_REASONING_SUMMARY`
- `TRANSLATION_MODEL`、`OPENAI_TRANSLATION_MODEL`、`TRANSLATION_BATCH_SIZE`
- `USER_PREFERENCE`、`EMAIL_SUBJECT`、`DRY_RUN`、`COMMIT_RUNTIME_STATE`

如果未配置对应 repository variables，workflow 默认使用 `MAX_CANDIDATES=80`、`MAX_SELECTED=15`、`OPENAI_API_MODE=responses`、`TARGET_LANGUAGE=zh-CN`、`TIMEZONE=Asia/Shanghai`、`DRY_RUN=1` 和 `COMMIT_RUNTIME_STATE=0`。

工作流会检出仓库、同步最新 `main`、安装依赖、运行 pipeline 并上传 digest artifact。只有在 `DRY_RUN` 不是 `1` 且 `COMMIT_RUNTIME_STATE=1` 时，才会把更新后的 state/logs 提交回 `main`。

提交 state/logs 前会 rebase 到最新 `main`，降低运行期间远端分支前进导致 push 失败的概率。

## 输出与日志

| 路径 | 用途 |
| --- | --- |
| `out/daily_digest.html` | 渲染后的邮件预览 |
| `out/processed_items.json` | 用于渲染 digest 的结构化文章数据 |
| `logs/latest.json` | 最近一次运行日志 |
| `logs/run-*.json` | 历史运行日志 |
| `state/sent_items.json` | 已发送签名，用于去重 |

`processed_items.json` 可用于恢复：如果 workflow 已经发出邮件，但在提交 state 前失败，可以用它补回已发送状态。

运行日志会在 `output_metrics.token_usage` 中记录 LLM 可观测信息，包括实际 API 模式、模型名、input/output token、缓存 input token、reasoning token、response ID，以及可选的 selection reasoning summary。`output_metrics.translation_integrity` 会记录期望翻译数、模型返回数、fallback 数和缺失 sig。邮件底部会显示精简的 selection/translation token 摘要。

## 常见问题

### 邮件已发送，但 workflow 提交 state 失败

检查上传的 artifact 中是否有 `processed_items.json` 和 `daily_digest.html`。当前 workflow 已在 push 前 rebase，但如果仍需手工恢复，可以把已发送文章的签名补入 `state/sent_items.json`。

### 入选文章太相似

可以调整 `MAX_PER_SOURCE`、`MAX_PER_TOPIC_CLUSTER`、`MIN_MATCH_SCORE`、`pipeline.user_preference` 或 `config/prompts.yaml`。

### 模型仍然选出不想看的类别

pipeline 已在 LLM 前做 Python 侧关键词过滤。如果仍有漏网内容，可以在 `scripts/news_pipeline.py` 的排除关键词列表中补充关键词，或增强 `pipeline.user_preference`。

### digest 数量太少

可以降低 `MIN_MATCH_SCORE`，提高 `MAX_CANDIDATES`，或放宽 `MAX_PER_SOURCE` / `MAX_PER_TOPIC_CLUSTER`。

## License

本项目遵循 `LICENSE` 中的许可条款。
