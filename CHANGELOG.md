# Changelog

## [Unreleased]

### Security (修复)

- 老板面板鉴权重做:
  - 删除 `?pass=` URL 传密码(密码不再出现在 URL/日志)
  - 空密码默认拒绝访问,需显式配置 `allow_empty_password=true`
  - 登录签发随机 session token(HttpOnly cookie),不再用明文密码当 cookie
  - 新增登出接口 `/owner/logout`
- 新增 9 个鉴权回归测试(`tests/test_auth.py`),覆盖上述漏洞场景

### Changed (重构)

- 意向评分与 LLM 解耦:
  - `extract_lead` 现在只做信息抽取(不再让 LLM 打分)
  - 新增纯规则评分引擎 `score_lead()`(预算+30/面积户型+25/时间线+20/联系方式+15/急切词+10,封顶100)
  - 急切词检测 `has_urgent()` 独立于 LLM 判断
- 对话上下文截断:只回传最近 N 轮(`max_conversation_turns`,默认 8),防止 token 无限增长
- `config.json` 缺失时回退 `config.example.json`,新环境可开箱即跑

### Added

- 评分引擎黄金样本集 `tests/lead_cases.json`(10 个真实/构造案例)+ 17 个规则测试
- GitHub Actions CI(pytest + ruff)