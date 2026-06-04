# OV 版本化方案分析：基于 Git 存储协议

## 背景

在 OV 中支持按 project 粒度的版本化管理，核心需求：

1. **Seedance Studio 多版本模块**：按 project 粒度提交、回滚，支持"非线性"编辑
2. **经验记忆项目**：获取某个记忆文件的历史版本

用 Git 原生语义表达：
```bash
# 按目录范围提交
git commit -m "projectA: update feature X" -- resource/projectA/

# 按目录范围回滚
git checkout abc1234 -- resource/projectA/

# 获取某个文件的历史版本
git show abc1234:resource/projectA/xxx.md
```

---

## 方案选型：Monorepo vs Polyrepo

### 核心对比

| 维度 | **Monorepo（单仓库）** <br> 所有 project 共享一个 `.git` | **Polyrepo（多仓库）** <br> 每个 project 独立 `.git` |
|------|----------------------------------------------------------|------------------------------------------------------|
| **版本管理** | 全局一条 commit 历史链，通过路径过滤查看 project 历史 | 每个 project 完全独立的 commit 历史、tag、分支 |
| **原子提交** | ✅ 天然支持：跨 project 关联变更一次提交，保证一致性 | ❌ 需协调多仓库，无法原子提交 |
| **共享资源提交** | ✅ `shared/` 目录与 project 可以一起提交追踪 | ❌ 共享资源需单独仓库管理，依赖复杂 |
| **独立提交** | ✅ `git commit -- projectA/` 只提交指定目录 | ✅ 天然支持 |
| **独立回滚** | ✅ `git checkout <sha> -- projectA/` 只还原目录 | ✅ `git reset/revert` 直接回退 |
| **文件历史查询** | ✅ `git show <sha>:path` 支持 | ✅ 支持 |
| **Tag 管理** | ⚠️ Tag 全局唯一，需命名约定（`projectA-v1.0`） | ✅ 每个仓库独立打 tag |
| **分支管理** | ⚠️ 分支全局，需约定前缀（`feature/projectA-xxx`） | ✅ 每个仓库独立分支 |
| **跨 project 协作** | ✅ 原子提交 + 共享代码容易 | ❌ 依赖管理复杂 |
| **存储效率** | ✅ blob 去重：相同内容的文件共享同一个 blob | ⚠️ 跨仓库无法去重 |
| **对象存储友好** | ✅ 所有对象在一个 `.git/objects/`，只需要 object get 操作 | ⚠️ 多个仓库的对象目录分散 |
| **权限控制** | ❌ 只能对整个仓库授权 | ✅ 可以对每个 project 单独授权 |
| **CI/CD** | ⚠️ 需要路径过滤只构建变更的 project | ✅ 每个仓库独立 CI |
| **维护复杂度** | ✅ 单一仓库，管理简单 | ⚠️ 多仓库管理开销大 |
| **并发写入** | ⚠️ 单仓库 HEAD 引用有并发冲突风险 | ✅ 多仓库天然隔离 |
| **冷启动速度** | ⚠️ 仓库大了 clone 慢（可用 shallow clone 优化） | ✅ 按需 clone，体积小 |

---

## 针对 OV 场景的深入分析

### 1. Session 历史的版本管理

**需求**：回滚后"不应做某事"的经验信息不应该丢失。

| 方案 | 说明 | Monorepo 适配度 | Polyrepo 适配度 |
|------|------|-----------------|-----------------|
| **分离存储**：对话历史存在独立的 `sessions/` 目录，不随 project 一起回滚 | 回滚 project 时 exclude sessions 目录 | ✅ 容易实现（路径过滤） | ⚠️ sessions 需要独立仓库，回滚逻辑复杂 |
| **提交时关联**：每次 commit 记录关联的 session_id，session 历史单独存储 | 需要在 commit message 或 note 中记录元数据 | ✅ 支持 git notes | ✅ 支持 |
| **Memory 系统持久化**：回滚前将"负向经验"写入 Memory 系统 | 与 git 方案解耦，由上层业务处理 | ✅ 无关 | ✅ 无关 |

**推荐**：采用"分离存储 + Memory 系统持久化"，Monorepo 更便于路径过滤。

### 2. 非线性编辑（类 Git 分支）

**需求**：不同角色同时编辑不同内容，无冲突自动 merge，有冲突单独解决。

| 特性 | Monorepo | Polyrepo |
|------|----------|----------|
| 多角色并行编辑 | ⚠️ 分支全局，需要约定前缀（`edit/projectA-光影调整`） | ✅ 每个 project 独立分支，天然隔离 |
| 分支合并 | ✅ `git merge` 支持 | ✅ 支持 |
| 冲突解决 | ⚠️ 冲突范围可能跨 project | ✅ 冲突局限在单个 project |
| 预览分支内容 | ✅ `git show branch:projectA/` | ✅ `git show branch:` |

### 3. 对象存储友好性

Git 对象存储的核心优势：**只需要 object get 操作，不需要遍历**。

- **Monorepo**：所有对象在同一个 `objects/` 命名空间，对象存储映射简单：
  ```
  .git/objects/aa/bbccdd... → s3://bucket/objects/aabbccdd...
  ```
- **Polyrepo**：每个仓库有独立的 `objects/`，需要增加仓库前缀：
  ```
  projectA/.git/objects/aa/bb... → s3://bucket/projectA/objects/aabb...
  ```

两者都可以映射到对象存储，但 Monorepo 的命名空间更统一。

---

## 风险与挑战

### Monorepo 风险

1. **仓库体积膨胀**：大量二进制素材文件会导致仓库快速变大
   - 缓解：使用 Git LFS，或将大文件存外部对象存储，git 只存引用
2. **并发写入冲突**：多用户同时提交可能导致 HEAD 引用冲突
   - 缓解：提交时使用乐观锁（compare-and-swap）或排队机制
3. **单仓库故障影响全局**：一个 project 的问题可能影响整个仓库
   - 缓解：定期备份，使用 reflog 恢复

### Polyrepo 风险

1. **跨 project 一致性**：需要原子修改多个 project 时无法保证
   - 缓解：引入分布式事务或两阶段提交，复杂度高
2. **依赖管理复杂**：project 之间共享代码需要版本协调
   - 缓解：使用包管理或 submodule，但增加了复杂度
3. **管理开销**：仓库数量多了之后，批量操作（CI 配置、权限、备份）繁琐
   - 缓解：使用 meta-repo 工具（如 `meta`、`repo`）管理

---

## 推荐方案

### 方案 A：Monorepo（推荐）

**适用场景**：
- project 之间有较强关联，经常需要一起修改
- 重视原子提交和一致性
- 希望管理简单，不想维护多仓库

**实现要点**：
```
ov-repo/
├── .git/
├── resource/
│   ├── projectA/
│   ├── projectB/
│   └── projectC/
├── shared/              # 共享素材、模板
└── sessions/            # 对话历史（独立于 project 回滚）
```

**关键实现**：
1. 提交：`git commit -m "projectA: xxx" -- resource/projectA/`
2. 回滚：`git checkout <sha> -- resource/projectA/`（exclude `sessions/`）
3. 历史：`git show <sha>:resource/projectA/xxx.md`
4. Tag：`projectA-v1.0`、`projectB-v2.3` 命名约定
5. 分支：`edit/projectA-xxx` 命名约定

---

### 方案 B：Polyrepo

**适用场景**：
- project 完全独立，很少跨 project 修改
- 需要细粒度权限控制
- 每个 project 有完全独立的发布节奏

**实现要点**：
```
ov-repos/
├── projectA/            # 独立仓库
│   ├── .git/
│   └── resource/
├── projectB/            # 独立仓库
├── shared/              # 独立仓库（共享资源）
└── sessions/            # 独立仓库
```

**关键实现**：
1. 每个 project 独立 init、commit、rollback
2. 跨 project 操作需要业务层协调（无法原子提交）
3. 对象存储需要增加仓库前缀

---

## 结论

**推荐 Monorepo 方案**，理由：

1. ✅ 符合 OV 的协作场景：project 之间有关联，需要原子提交
2. ✅ 对象存储映射简单，符合"只需要 object get"的设计目标
3. ✅ blob 去重节省存储空间（重复素材只存一次）
4. ✅ 管理简单，降低运维复杂度
5. ⚠️ 仓库体积和并发问题有成熟的缓解方案（LFS、乐观锁）

如果未来某些 project 确实需要完全独立，可以再拆分出去。
