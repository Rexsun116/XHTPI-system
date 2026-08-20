# XHTPI-system：Codex 长期开发规则

本文档适用于本项目后续的 Codex 开发工作。除非用户明确指示，否则必须遵守以下规则。

## Git 与变更范围

- 当前开发分支为 `feature/codex-rebuild`。
- `main` 是稳定基线；不得直接在 `main` 上进行开发性修改。
- 未经明确要求，不执行 `git push`，不合并到 `main`，也不创建提交。
- 修改应尽量小步进行：完成一个功能、完成相应验证并报告后，再开始下一项功能。
- 未经明确要求，不删除现有功能、历史模板或业务字段。
- 不要把生成的 PDF、DOCX、数据库、缓存、虚拟环境或 `.DS_Store` 提交到 Git。保持并遵循 `.gitignore`：尤其是 `instance/*.db`、`static/invoices/`、`static/packing_lists/`、`static/booking_docs/`、`venv/`、`.venv/` 和各类缓存。

## 先理解，再修改

- 修改任何功能前，必须阅读相关 Flask route、SQLAlchemy model、Jinja2 template 及其调用的业务流程；不得仅根据页面或文件名猜测逻辑。
- 对业务含义、状态流转、外贸单据字段或数据保留规则不确定时，先询问用户，不得自行假设业务规则。
- 如发现明显 bug、安全风险、重复逻辑或架构问题，可以说明证据并提出方案；与当前任务无关时，不要顺手进行大规模修改。
- 本项目核心代码集中于 `app.py` 不代表可以任意拆分或重构。任何重构必须先提出范围、风险、兼容性与分阶段方案，获得确认后逐步执行。

## 数据库与数据安全

- 严禁删除、覆盖、重建或用测试数据替换真实业务数据库。
- `instance/database.db` 是本地业务数据，不纳入 Git；不得将其加入版本控制。
- 不运行会删除业务数据的脚本或命令，除非用户明确指定目标、确认影响并授权执行。特别注意根目录 `delete_pi.py` 会按 PI 编号删除记录，不能作为日常验证手段。
- 任何数据库结构变化前，必须先向用户说明并等待确认：
  1. 为什么需要修改；
  2. 涉及哪些 SQLAlchemy model 和字段；
  3. 对现有数据、关联关系及生成单据的影响；
  4. Alembic migration 与必要的数据迁移/回滚方案。
- 在方案确认前，不执行破坏性数据库操作，也不执行可能重建、清空、覆盖或不可逆修改数据库的 migration。
- 数据库结构改动应通过 `migrations/versions/` 中的 Alembic migration 管理；同时核对 model、migration 与现有 SQLite 数据库的实际结构是否一致。

## 业务兼容性

- PI、Invoice、Packing List、Booking、BL、COC、SGS 等均属于真实外贸业务流程。修改时以既有业务数据和单据兼容为优先目标。
- `PI` 与 `PIItem` 保存客户、出口商、产品和工厂的快照字段；变更主数据、编辑订单、数据迁移或生成单据时，必须同时评估这些快照的历史一致性。
- PI 的状态及装运、到港、结算相关字段会驱动业务流程和待办提醒。调整字段、状态选项或模板展示前，必须追踪创建、编辑、状态更新、列表/仪表盘统计及文档生成的全链路。
- Word 托书模板位于 `templates/word/BN-Sample.docx`；生成的 Booking 文档、Invoice、Proforma Invoice 和 Packing List 分别写入 `static/booking_docs/`、`static/invoices/`、`static/packing_lists/`。不要覆盖已有业务文件；新增生成逻辑时应保持命名、下载和存储行为兼容。

## UI 规则

- UI 修改必须延续当前已完成的新版 Sidebar / Dashboard 设计语言：以 `templates/base.html` 的固定侧边栏、Dashboard 卡片、Bootstrap 5、Font Awesome 和 Inter 字体为基础。
- 不要恢复旧版顶部导航，也不要绕开 `base.html` 的布局、移动端侧边栏行为或 Jinja block 结构。
- 修改页面时，先检查对应 template 的继承关系、传入变量、`url_for`/路由名称以及 `static/style.css` 和页面内样式的影响。

## 项目结构与技术栈（基于当前代码）

- **应用核心**：`app.py` 是单文件 Flask 单体应用，定义 Flask app、Flask-Login、SQLAlchemy models、业务路由、统计/提醒、文档生成和启动入口。
- **技术栈**：Python 3.13（`Pipfile`），Flask 3、Flask-SQLAlchemy、SQLAlchemy 2、Flask-Migrate/Alembic、Flask-Login、Jinja2、Bootstrap 5、Font Awesome、python-docx、WeasyPrint 和 requests。依赖清单见 `requirements.txt` 与 `Pipfile`。
- **模板**：`templates/` 存放 Jinja2 页面；`base.html` 是全局 Sidebar 布局；`_pi_base_fields.html` 为 PI 共用字段；`proforma_invoice.html`、`invoice.html`、`packing_list.html` 用于 PDF 渲染；`templates/word/` 存放 Word 模板。
- **静态与生成文件**：`static/style.css` 为共享样式；业务生成物写入 `static/invoices/`、`static/packing_lists/` 与 `static/booking_docs/`，均为本地生成文件。
- **数据库与迁移**：SQLite URI 配置为 `sqlite:///database.db`，在 Flask 的 instance 目录下对应 `instance/database.db`；`migrations/` 存放 Alembic 配置与历史迁移。
- **辅助脚本与文档**：`delete_pi.py` 是会删除指定 PI 的高风险脚本；`docs/` 存放开发文档。

## 本地启动与验证

- 当前应用入口在 `app.py`：在已安装项目依赖且已确认本地数据库安全的前提下，可用 `python app.py` 启动开发服务器。该入口启用 `debug=True`，并会调用 `db.create_all()`；不要把它当作数据库迁移或生产运行方案。
- 每次代码修改后，至少执行并如实报告实际执行过的验证：
  1. Python 语法检查（例如 `python -m py_compile app.py`）；
  2. 涉及路由时，检查 route、端点名、`url_for` 与模板引用；
  3. 涉及模板时，检查 Jinja2 继承、变量、条件分支和循环的上下文；
  4. 尽可能运行与改动直接相关的测试或受控的手工验证。
- 当前仓库未发现独立的自动化测试目录或 pytest 配置；`templates/test_update_pi.html` 是模板文件，并不等同于自动化测试。若无法安全地运行应用或测试，必须明确说明未验证的范围和原因，绝不伪造测试结果。

## 当前架构注意事项

- `app.py` 在请求前与启动入口均会调用 `db.create_all()`，而项目同时维护 Alembic migration；做 schema 相关工作前必须谨慎核对 migration、模型和已有数据库，避免由自动建表掩盖迁移缺失或造成结构漂移。
- 历史 migration 中存在 `order_type` / `pi_type` 的变更，而当前 `PI` model 未定义这两个字段；数据库结构调整前必须先检查真实数据库与迁移链，不能假设三者一致。
- 多个删除 route 使用 GET 请求，且 `delete_pi.py` 可直接删除 PI；修改访问控制、删除行为或相关模板时，应把数据安全与兼容性作为独立评估项。
- 当前 `app.py` 包含硬编码的 `secret_key` 和初始账号创建/凭据逻辑。这是应优先提出的安全风险；除非当前任务明确要求，先报告和给出迁移方案，不要在无关改动中直接替换认证或凭据机制。
