# 装修获客 AI 客服 · renovation-bot

给本地装修公司做的一站式 **AI 获客系统**:客户来咨询,自动回答报价/工期/案例,顺手把高意向客户的联系方式推到老板手机上。

> 卖的不是"AI 客服",是**"替你接住每一个装修客户"**。

## 完整链路

```
客户咨询 → LLM 只做信息抽取(面积/户型/预算/时间/联系方式/急切词)
        → 纯规则评分引擎(预算30/面积户型25/时间线20/联系方式15/急切词10,封顶100)
        → 意向分级(≥70高 / 40-69中 / <40低) → SQLite 落库
        → 老板面板(/owner) + 高意向钉钉推送(24h 去重)
```

**为什么 LLM 不参与打分?** 打分规则写在 prompt 里时,模型随机性 = 业务规则随机性;
拆成"LLM 只抽取 + Python 规则引擎打分"后,同一句话永远得到同一分数,可用黄金样本集回归测试(`tests/lead_cases.json`, 10 个案例)。

## 三个入口(甲方视角)

| 入口 | 地址 | 给谁用 |
|---|---|---|
| 客户聊天页 | `http://IP:8765/` | 客户扫码 / 点链接,问装修问题 |
| 老板面板 | `http://IP:8765/owner` | 老板手机随时看:今天几个客户、谁高意向 |
| 钉钉推送 | 老板建个群挂机器人 | 高意向客户自动弹消息 |

## 为什么是"获客系统"而不是"聊天机器人"

- 客户说 "89平三房,预算15万,十月装" → 自动识别为**高意向**
- 客户说 "随便问问" → **低意向**,不打扰老板
- 客户留下联系方式 → **钉钉实时响**,老板只负责回电话
- 每个会话累积成一张客户卡,面板能看到全部流量:"今日咨询 17 / 高意向 4 / 待跟进 8"

## 换客户 = 换两个文件

`kb_client.md`(客户知识库:报价/工期/案例/FAQ)+ `config.json`(公司名/客服名/钉钉webhook/面板密码)。

配套 [`客户资料采集模板.md`](客户资料采集模板.md):老板填资料,你只负责导入,10 分钟换一家。

## 技术栈

| 层 | 用什么 |
|---|---|
| Web | Flask(客户页 + 老板面板) |
| 大模型 | DeepSeek(问答 + 结构化留资抽取) |
| 意向评分 | 纯规则引擎(确定性,可测试) |
| 存储 | SQLite(每操作独立连接,线程安全) |
| 通知 | 企业微信 / 钉钉 群机器人 webhook(自动识别) |

## 测试与质量

```bash
pip install pytest ruff
pytest tests/ -q    # 25 个用例:评分黄金样本 + 鉴权回归
ruff check .        # 静态检查
```

- 评分引擎不依赖 LLM,离线可测(`tests/test_lead_scoring.py`)
- 鉴权回归覆盖历史漏洞场景:URL 传密码、空密码放行、明文 cookie(`tests/test_auth.py`)
- GitHub Actions 自动跑 pytest + ruff

## 快速开始(本地)

```bash
./deploy.sh             # 客户页 http://0.0.0.0:8765/
./deploy.sh --owner     # 顺便打开老板面板
python3 leadgen.py --history '["89平三房,预算15万","微信是138xxxx"]'   # 单测留资抽取
```

配置:复制 `config.example.json` 为 `config.json`,填 DeepSeek key(`~/.hermes/.env` 或环境变量)与钉钉 webhook。

## 部署到服务器(轻量云,约 1 小时)

```bash
# 本机上传
rsync -av --exclude '.venv' --exclude 'leads.db' ./ root@服务器IP:/opt/renovation-bot/
# 服务器上跑一次(装依赖 + systemd 常驻 + 自检)
ssh root@服务器IP "cd /opt/renovation-bot && bash setup_server.sh"
# 云控制台【安全组/防火墙】放行 TCP 8765
```

## 文件结构

```
renov_bot_web.py        # Flask 主应用:客户页 + /ask + 老板面板
leadgen.py              # 留资抽取 / 意向分级 / SQLite 落库 / 钉钉推送(可独立单测)
kb_client.md            # 客户知识库(换客户就换它)
config.json             # 每客户配置(gitignored,含 webhook 密钥)
deploy.sh               # 本地一键启动
setup_server.sh         # 服务器一键配置(systemd 常驻)
商用说明.md              # 交付定价与流程
客户资料采集模板.md       # 给老板填的采集清单
```

> `kb.md`(业主端谈判知识库)、`config.json`、`leads.db` 属私人数据,不入库。

## 项目状态

- **V0 聊天机器人** ✅
- **V1 获客漏斗 MVP** ✅(本地 + 云端部署跑通)
- **V1.5 真实客户交付** ← 当前:拿 demo 见第一个装修老板,299 案例价换真实案例
- **V1.6 工程化** ✅(规则评分引擎 / 鉴权重做 / 25 测试 / CI)
- **V2 AI 销售助手**(自动追问、意向引导)——有真实数据后启动

## License

[Elastic License 2.0](LICENSE)（Source Available）：

- ✅ 可浏览、可学习、可自用、可修改
- ❌ 不得将本软件作为服务提供给第三方（即不能拿这份源码直接架站对外卖）

商用 / 交付合作请联系作者洽谈。
