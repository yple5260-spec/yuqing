# 投诉治理“换芯计划”系统源码

用于重点舆情事件建档、域外评分规则配置、图片证据留存、独立复核及可解释 CSV 导出。当前为单节点受控试点，不应直接面向公网部署。

## 源码交付范围

本仓库仅包含前后端源码、启动脚本、依赖、测试及技术说明，**不包含 PPT、真实客户档案、原始工作簿、真实舆情截图、运行数据库、密码或加密密钥**。

`seed/` 为本机私有演示素材目录，已忽略；源码全新检出后没有预置客户档案，需通过系统导入或手动建档。不要将真实业务数据加入 Git。

## 天眼平台接入

已增加 PostgreSQL、app_managed 独立登录、双入口路径和容器适配。具体配置、SQLite迁移、备份和检查范围见 [天眼部署](docs/天眼部署.md)。2026-11-10 已将用户提供的真实 SQLite 数据迁入平台 PostgreSQL，逐表内容、原密钥及附件校验通过；原库和迁移前备份保留。同日12份加密证据已迁入私有S3，旧文件保留；平台容器发布尚未执行。

## 启动

需要 Python 3.11 或以上版本。在仓库目录执行：

```sh
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows 使用 .venv\Scripts\activate
python -m pip install -r requirements.txt
python launcher.py
```

也可运行对应平台的“启动系统”脚本。默认地址为 `http://127.0.0.1:8765`。首次启动随机生成账号凭据，保存到 `data/首次登录凭据.txt`；该目录不受 Git 管理。若设置 `XIN_DATA_DIR`，凭据及业务文件写入指定目录。

## 目录

- 当前目录已连接 `https://github.com/yple5260-spec/yuqing.git`；本机使用 `.venv` 和未提交的 `.env`。
- `prototype/original/index.html`：原样保留的最早六菜单原型，使用构造数据，与业务系统独立；见 [原型说明](prototype/README.md)。
- `资料/`、`local-data/`、`runtime/`：本地需求资料、原SQLite备份及运行文件，排除出Git和镜像；历史演示和临时交付已移出，见 [整理记录](docs/目录整理.md)。
- `server.py`：业务 API、访问控制、存储、导入导出及审计。
- `scoring.py`：评分规则校验、证据约束及得分区间。
- `static/`：HTML、CSS、JavaScript 和中国联通标识。
- `launcher.py`、启动脚本：本机启动入口。
- `manage.py`：停机备份及备份校验。
- `tests/test_system.py`：评分单元测试及端到端测试。
- `docs/部署与对接说明.md`：部署变量、API 及上线边界。
- `docs/客服导出字段映射_待目标方确认.csv`：目标系统对接字段草案。

## 测试

不依赖客户素材的评分单元测试：

```sh
python -m unittest discover -s tests -p test_system.py -k ScoringTests -v
```

现有完整端到端测试还依赖本机私有 `seed/` 目录中的 12 条演示素材（包括 `samples.json`、`H44.jpg`、`H46.jpg`）；**这些真实素材不随源码提交**。仅在有权使用并已安全配置该目录的环境执行：

```sh
python -m unittest discover -s tests -v
```

端到端测试使用临时数据库，不修改正式运行数据。源码仓库未附带真实素材，不代表已在无素材环境完成全部端到端验证。

## 能力边界

- “辅助摘要”当前为本地模板，尚未调用大模型或向外部模型发送用户数据。
- 证据通过真实截图上传留存，不提供绕过平台登录、验证码或权限限制的采集。
- 客服对接为 CSV 导出和人工回执；尚未直连目标客服系统。
- 业务字段及文件加密不等于全库加密；上线前仍需完成企业身份接入、TLS 网关、密钥托管、安全与业务验收。

详细部署说明见 `docs/部署与对接说明.md`。本机旧版交付说明中提及的真实样本、截图和验收附件不属于本源码仓库。
