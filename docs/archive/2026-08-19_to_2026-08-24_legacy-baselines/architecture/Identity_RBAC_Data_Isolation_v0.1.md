# 用户、RBAC 与数据隔离设计 v0.1

## 技术基线

- 前端：React 19 + Ant Design 5 + Ant Design ProComponents。
- 后端：FastAPI、OAuth2 Bearer/JWT、Argon2 密码哈希。
- 数据库：SQL Server 2014，使用既有 `iam` Schema。

## 身份与会话

`iam.app_user` 保存本地或外部身份、状态、部门和密码哈希；`iam.auth_session` 保存JWT的JTI、到期和撤销状态；`iam.login_audit`记录登录结果。注册账户默认为`PENDING`，管理员启用并分配角色后才能登录。

## 权限模型

权限只在后端判定。前端菜单依据同一权限列表做可见性处理，但隐藏菜单不等于授权。

- 用户与角色：`iam.user_role`
- 角色与权限：`iam.role_permission`
- 数据范围：`iam.data_scope_grant`

首批角色包括系统管理员、数据管理员、CP工程师、FT工程师、质量工程师、管理只读和审计运维。

## 数据隔离

`dataset.dataset.owner_user_id`是数据所有者。非所有者访问数据时，必须命中用户或其角色的数据范围授权：

- `GLOBAL`：全部数据；
- `DEPARTMENT`：数据所有者所属部门；
- `PROJECT`：项目编码；
- `PRODUCT`：产品；
- `SUPPLIER`：晶圆厂、封测厂或来源；
- `OWNER`：本人数据。

数据集列表、结果、图表和导出均在FastAPI查询层应用相同范围条件。浏览器提交的owner、角色或范围值不能绕过后端判断。
