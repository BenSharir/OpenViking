# Monorepo vs Polyrepo 对比

## 核心概念

| 模式 | 说明 |
|------|------|
| **Monorepo（单仓库）** | 所有 project 共享同一个 `.git` 仓库，通过目录隔离 |
| **Polyrepo（多仓库）** | 每个 project 独立 `.git` 仓库，完全隔离 |

---

## 详细对比表

| 维度 | Monorepo（单仓库） | Polyrepo（多仓库） |
|------|-------------------|-------------------|
| **版本历史** | 全局一条 commit 历史链，通过路径过滤查看单个 project 历史 | 每个 project 完全独立的 commit 历史 |
| **Tag 管理** | Tag 全局唯一，需要命名约定区分（`projectA-v1.0`） | 每个仓库独立打 tag，互不干扰 |
| **分支管理** | 分支全局，需要约定前缀（`feature/projectA-xxx`） | 每个仓库独立分支，无冲突 |
| **独立提交** | ✅ 支持 `git commit -- projectA/` 只提交指定目录 | ✅ 天然支持，每个仓库独立 commit |
| **独立回滚** | ✅ `git checkout <sha> -- projectA/` 只还原目录 | ✅ `git reset/revert` 直接回退 HEAD |
| **跨 project 协作** | ✅ 原子提交多个关联变更，保证一致性 | ❌ 需要协调多个仓库版本，依赖管理复杂 |
| **代码共享** | ✅ 直接引用同一仓库内的目录 | ❌ 需要通过包管理（npm/pip）或 submodule |
| **仓库体积** | ⚠️ 单仓库体积增长快，clone 慢 | ✅ 每个仓库小，clone 快 |
| **权限控制** | ❌ 只能对整个仓库授权 | ✅ 可以对每个仓库单独授权 |
| **CI/CD** | ⚠️ 需要路径过滤只构建变更的 project | ✅ 每个仓库独立 CI，触发精准 |
| **发布节奏** | ⚠️ 需要约定或全局版本号 | ✅ 每个 project 完全独立发布 |
| **Git 性能** | ⚠️ 文件/历史极多时可能变慢 | ✅ 每个仓库小，操作快 |
| **本地检出** | ⚠️ 默认检出全部代码（可用 sparse-checkout 优化） | ✅ 按需 clone，占用空间小 |
| **Issue/PR 管理** | ⚠️ 所有 project 的 Issue/PR 混在一起 | ✅ 每个仓库独立管理 |

---

## Monorepo 按目录提交的工作流

### 目录结构
```
monorepo/
├── .git/
├── projectA/
├── projectB/
├── projectC/
└── shared/
```

### 常用命令

#### 只提交单个 project
```bash
# 方法 1：先 add 再 commit
git add projectA/
git commit -m "projectA: add feature X"

# 方法 2：直接 commit 指定目录（会自动 add 已跟踪的文件）
git commit -m "projectA: fix bug" -- projectA/
```

#### 查看单个 project 的历史
```bash
git log --oneline -- projectA/
```

#### 回滚单个 project
```bash
# 恢复 projectA 到指定 commit（不影响其他目录）
git checkout abc1234 -- projectA/
git commit -m "projectA: rollback to abc1234"
```

#### 对比两个版本间某个 project 的变化
```bash
git diff v1.0 v2.0 -- projectA/
```

#### 原子提交多个关联变更
```bash
# 同时修改 projectA 和 shared 库，一次提交保证一致性
git add projectA/ shared/
git commit -m "projectA: use new shared API"
```

---

## 选型建议

### 选 Monorepo 如果你：
- project 之间联系紧密，经常需要一起修改
- 希望原子性提交跨 project 的变更
- 团队规模不大，或有 monorepo 工具链（Bazel、Nx、Turborepo）支持
- 看重代码共享和一致性

### 选 Polyrepo 如果你：
- project 相对独立，各自有发布节奏
- 需要细粒度的权限控制
- 不同 project 由不同团队维护
- 希望每个仓库保持轻量

---

## 折中方案

如果某些 project 关联紧密，另一些独立，可以：
- **分组 Monorepo**：按业务域分成多个 Monorepo
- **Monorepo + Submodule**：核心仓库用 Monorepo，外部依赖用 submodule
