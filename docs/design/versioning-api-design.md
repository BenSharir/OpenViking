# OV 版本化 API 设计方案

## 背景

基于 Git 协议为 OV 实现按目录（project）粒度的版本管理，支持：
1. Seedance Studio：按 project 粒度提交、回滚，支持非线性编辑
2. 经验记忆项目：获取记忆文件的历史版本

## 架构设计

### 核心概念

| 概念 | Git 对应 | 说明 |
|------|----------|------|
| Snapshot | Commit | 目录在某个时间点的完整快照 |
| Commit ID | SHA-1 | 快照的唯一标识（40 位十六进制） |

### 存储方案

采用 **Monorepo** 方案：
- 所有 project 共享一个 Git 仓库（通过自定义 ObjectStore 适配 VikingFS）
- 按目录范围提交，相当于 `git commit -- <path>`
- 按目录范围回滚，相当于 `git checkout <sha> -- <path>`

```
viking://resources/
├── projectA/           # project 目录
├── projectB/
└── .git/               # 共享的 git 数据（可存 VikingFS 或外部对象存储）
    ├── objects/        # blob/tree/commit 对象
    └── refs/           # HEAD 等引用
```

---

## API 设计

### 新增方法（AsyncOpenViking）

```python
# ============= Snapshot methods =============

async def create_snapshot(
    self,
    uri: str,
    message: str = "",
    wait: bool = True,
    timeout: Optional[float] = None,
    telemetry: TelemetryRequest = False,
    include_vectors: bool = True,
) -> Dict[str, Any]:
    """
    创建目录快照（提交）。

    相当于 git commit -m <message> -- <uri>

    Args:
        uri: 要快照的目录 URI（如 viking://resources/projectA）
        message: 快照说明
        wait: 是否等待处理完成
        timeout: 超时时间（秒）
        telemetry: 是否附加操作遥测数据
        include_vectors: 是否包含向量（默认 True，将向量打包进 blob）

    Returns:
        {
            "commit_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            "short_id": "a1b2c3d4",
            "message": "projectA: add login feature",
            "timestamp": 1717209600,
            "files_changed": 5,
        }
    """

async def get_snapshot(
    self,
    uri: str,
    commit_id: str,
    *,
    include_files: bool = True,
) -> Dict[str, Any]:
    """
    获取快照详情。

    Args:
        uri: 目录 URI
        commit_id: 提交 ID（完整 40 位或短 8 位）
        include_files: 是否返回文件列表（默认 True）

    Returns:
        {
            "commit_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            "short_id": "a1b2c3d4",
            "message": "projectA: add login feature",
            "author": "User <user@example.com>",
            "committer": "User <user@example.com>",
            "timestamp": 1717209600,
            "timezone": "+0800",
            "parents": ["0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b"],
            "tree_id": "8a3a0e5c7b1d9f2a4b6c8d0e1f2a3b4c5d6e7f89",
            "uri": "viking://resources/projectA",
            "file_count": 12,
            "files": [
                {"path": "docs/README.md", "size": 2048, "mode": "100644", "type": "blob", "sha": "64666139..."},
                {"path": "src", "mode": "040000", "type": "tree", "sha": "32366132..."},
            ],
            "stats": {
                "total_files": 12,
                "total_size": 102400,
                "dirs": 3,
            },
        }
    """

async def restore_snapshot(
    self,
    uri: str,
    commit_id: str,
    *,
    create_new: bool = False,
    reindex: bool = True,
    wait: bool = True,
    timeout: Optional[float] = None,
    telemetry: TelemetryRequest = False,
) -> Dict[str, Any]:
    """
    回滚目录到指定快照。

    相当于 git checkout <commit_id> -- <uri>

    Args:
        uri: 要回滚的目录 URI
        commit_id: 目标提交 ID
        create_new: 如果为 True，不修改原目录，而是创建一个新目录（uri + "_backup"）
        reindex: 是否重新构建向量索引（默认 True）
        wait: 是否等待处理完成
        timeout: 超时时间（秒）
        telemetry: 是否附加操作遥测数据

    Returns:
        {
            "commit_id": "b2c3d4e5f6a7...",
            "files_restored": 5,
            "new_uri": "viking://resources/projectA_backup",  # 仅 create_new=True 时
        }
    """

async def get_file_at_snapshot(
    self,
    uri: str,
    file_path: str,
    commit_id: str,
    *,
    include_vector: bool = False,
) -> Dict[str, Any]:
    """
    获取某个文件在指定快照的内容。

    相当于 git show <commit_id>:<file_path>

    Args:
        uri: 目录 URI
        file_path: 相对于 uri 的文件路径
        commit_id: 提交 ID
        include_vector: 是否同时返回向量

    Returns:
        {
            "content": "file content...",
            "size": 1024,
            "vector": [0.1, 0.2, ...],  # 仅 include_vector=True 时
        }
    """
```
    """
    获取某个文件在指定快照的内容。

    相当于 git show <commit_id>:<file_path>

    Args:
        uri: 目录 URI
        file_path: 相对于 uri 的文件路径
        commit_id: 提交 ID
        include_vector: 是否同时返回向量

    Returns:
        {
            "content": "file content...",
            "size": 1024,
            "vector": [0.1, 0.2, ...],  # 仅 include_vector=True 时
        }
    """

async def copy_snapshot_to(
    self,
    src_uri: str,
    dest_uri: str,
    commit_id: str,
) -> Dict[str, Any]:
    """
    将指定快照中某个目录的内容复制到新目录。

    相当于检出指定版本到新位置，不修改原目录。

    Args:
        src_uri: 源目录 URI（相对于仓库根目录）
        dest_uri: 目标目录 URI
        commit_id: 源提交 ID

    Returns:
        {
            "dest_uri": "viking://resources/projectA_backup",
            "files_copied": 12,
            "total_size": 102400,
        }
    """
```
```

---

## 接口汇总

| HTTP 方法 | 路径 | 功能 | 请求参数 | Git 等价 |
|----------|------|------|----------|----------|
| POST | `/api/v1/snapshot/create` | 创建目录快照 | **Body**: `uri`(必填), `message`, `wait`, `timeout`, `include_vectors` | `git commit -m <message> -- <uri>` |
| GET | `/api/v1/snapshot/get` | 获取快照详情 | **Query**: `uri`(必填), `commit_id`(必填), `include_files` | `git show <commit_id>` |
| POST | `/api/v1/snapshot/restore` | 回滚到指定快照 | **Query**: `commit_id`(必填)<br>**Body**: `uri`(必填), `create_new`, `reindex`, `wait`, `timeout` | `git checkout <commit_id> -- <uri>` |
| POST | `/api/v1/snapshot/copy` | 将快照复制到新目录 | **Query**: `commit_id`(必填), `src_uri`(必填), `dest_uri`(必填) | 检出指定版本到新位置 |
| GET | `/api/v1/snapshot/file` | 获取快照中的文件内容 | **Query**: `uri`(必填), `file_path`(必填), `commit_id`(必填), `include_vector` | `git show <commit_id>:<file_path>` |

所有接口返回统一格式：
```json
{
    "status": "ok",
    "result": { ... },
    "error": null,
    "telemetry": null
}
```

---

## 内部架构

### 模块划分

```
openviking/
├── versioning/
│   ├── __init__.py
│   ├── service.py          # VersioningService：核心业务逻辑
│   ├── store.py            # GitObjectStore：适配 VikingFS 的对象存储
│   ├── refs.py             # RefStore：引用存储
│   └── models.py           # 数据模型：Snapshot
└── service/
    └── versioning_service.py  # （可选）集成到现有 service 层
```

### 核心类

#### VersioningService

对外暴露的版本化服务，封装所有版本操作。

```python
class VersioningService:
    def __init__(self, viking_fs: VikingFS, object_store: GitObjectStore):
        self.viking_fs = viking_fs
        self.object_store = object_store

    async def snapshot(self, uri: str, message: str, ...) -> str:
        """创建快照。"""
        # 1. 遍历 uri 下的所有文件
        # 2. 为每个文件创建 blob（可选：打包向量）
        # 3. 构建 tree
        # 4. 创建 commit
        # 5. 更新 ref

    async def restore(self, uri: str, commit_id: str, ...) -> None:
        """回滚到快照。"""
        # 1. 读取目标 commit 的 tree
        # 2. 遍历 tree，恢复文件内容和向量
        # 3. 提交回滚操作
        # 4. 可选：触发 reindex
```

#### GitObjectStore

继承 Dulwich 的 `BaseObjectStore`，适配 VikingFS 或外部对象存储。

```python
class GitObjectStore(BaseObjectStore):
    def __init__(self, fs_api: VikingFSApi):
        self.fs_api = fs_api

    def __contains__(self, sha1) -> bool:
        return self.fs_api.exists(f"git/objects/{sha1.hex()}")

    def add_object(self, obj) -> None:
        self.fs_api.write(f"git/objects/{obj.id.hex()}", obj.as_legacy_object())

    def get_raw(self, sha1) -> tuple[int, bytes]:
        data = self.fs_api.read(f"git/objects/{sha1.hex()}")
        return parse_git_object(data)
```

---

## Git 对象格式

所有 Git 对象都遵循通用格式：先拼接头 + 内容，再对整个字节流做 SHA-1 计算对象 ID。

```
<type> <size>\0<content>
```

- `<type>`：`blob` / `tree` / `commit` / `tag`（ASCII 字符串）
- `<size>`：内容的字节数（十进制 ASCII）
- `\0`：空字节分隔符
- `<content>`：对象实际内容

### Blob（文件内容）

存储文件的原始二进制内容，不包含文件名或路径。

**格式**：
```
blob <size>\0<raw-bytes>
```

**业务示例**：剧本文件 `剧本.md` 内容为 `"第1集：主角登场..."`，对应的 blob 为：
```
blob 18\0第1集：主角登场...
```
SHA-1: `3b2c5d7e9f1a3b5c7d9e1f3a5b7c9d1e3f5a7b9c`

**特性**：
- 相同内容的文件（无论路径如何）共享同一个 blob，实现去重
- 例如 `projectA/剧本.md` 和 `projectB/备份/剧本.md` 如果内容相同，只会存储一份 blob

### Tree（目录结构）

存储目录条目列表，每个条目指向 blob 或子 tree。

**二进制格式**（每条目）：
```
<mode><space><name>\0<20-byte-SHA-1>
```

- `<mode>`：6 位八进制字符串
  - `100644`：普通文件
  - `100755`：可执行文件
  - `120000`：符号链接
  - `040000`：子目录（tree）
  - `160000`：gitlink（子模块）
- `<name>`：文件名/目录名（不含路径分隔符）
- `<SHA-1>`：20 字节二进制 SHA-1

**业务示例**：剧本项目 `projectA/` 的目录结构：
```
projectA/                     # tree A (root)
├── 剧本.md                   # blob: 3b2c5d7e...
├── 分镜.md                   # blob: 7e9f1a3b...
└── 素材/                      # tree B (子目录)
    ├── 场景1.png             # blob: a3b5c7d9...
    └── 参考/                  # tree C (嵌套子目录)
        └── 人物设定.png       # blob: d5f7a9c1...
```

对应的 tree 嵌套关系：

**Tree A（projectA/）**：
```
100644 剧本.md\0<3b2c5d7e...>    # 指向 剧本.md 的 blob
100644 分镜.md\0<7e9f1a3b...>    # 指向 分镜.md 的 blob
040000 素材\0<c5d7e9f1...>      # 指向 素材/ 子 tree B
```

**Tree B（素材/）**：
```
100644 场景1.png\0<a3b5c7d9...>  # 指向 场景1.png 的 blob
040000 参考\0<e9f1a3b5...>       # 指向 参考/ 子 tree C
```

**Tree C（素材/参考/）**：
```
100644 人物设定.png\0<d5f7a9c1...> # 指向 人物设定.png 的 blob
```

**嵌套关系图示**：
```
Commit A
  ↓
Tree A (projectA/)
  ├── blob: 剧本.md
  ├── blob: 分镜.md
  └── Tree B (素材/)
        ├── blob: 场景1.png
        └── Tree C (参考/)
              └── blob: 人物设定.png
```

### Commit（提交）

存储指向一个 tree 的引用 + 元数据（作者、提交者、父提交、消息）。

**文本格式**（UTF-8，每行 LF 结尾）：
```
tree <sha1>
parent <sha1>          # 0 或多个，初始提交无 parent
author <name> <email> <unix-timestamp> <timezone>
committer <name> <email> <unix-timestamp> <timezone>
gpgsig <signature>     # 可选，PGP 签名
                       # 空行
<commit-message>
```

**业务示例**：编剧提交了剧本的初版：
```
tree 2e4f6a8c0e2a4c6e8a0c2e4f6a8c0e2a4c6e8a0c
parent 0000000000000000000000000000000000000000  # 初始提交无父
author 张编剧 <zhang@example.com> 1717209600 +0800
committer 张编剧 <zhang@example.com> 1717209600 +0800

剧本：第1集初版，包含主角登场场景
```

第二次提交（修改了剧本）：
```
tree 9f1b3d5e7f9a1b3d5f7e9a1b3d5f7e9a1b3d5f7e
parent 2e4f6a8c0e2a4c6e8a0c2e4f6a8c0e2a4c6e8a0c  # 指向第一次提交
author 张编剧 <zhang@example.com> 1717296000 +0800
committer 张编剧 <zhang@example.com> 1717296000 +0800

剧本：修改第1集对话，增加冲突情节
```

### Index（暂存区）

Index（又称 Staging Area / Cache）是 Git 的暂存区域，存储即将提交的文件快照。

**存储位置**：`.git/index`

**业务示例**：编剧修改了 `剧本.md` 后执行 `git add 剧本.md`，此时 index 中会新增一条目：

| 字段 | 值 | 说明 |
|------|-----|------|
| mtime | 1717296000 | 文件修改时间 |
| mode | 100644 | 普通文件 |
| size | 2048 | 文件大小 |
| sha1 | 3b2c5d7e... | 文件内容的 blob SHA-1 |
| 路径 | projectA/剧本.md | 文件路径 |

**作用**：
1. 暂存 `git add` 的文件，下次 commit 时直接从 index 构建 tree
2. 记录工作区文件的 stat 信息，用于快速检测文件变更
3. 支持合并冲突标记

### 对象关系图

```
HEAD -> refs/heads/main -> commit A -> tree (root)
                                    |
                                    +-- blob (README.md)
                                    +-- blob (main.c)
                                    +-- tree (src)
                                    |   |
                                    |   +-- blob (util.c)
                                    |   +-- blob (util.h)
                                    |
                                    +-- tree (tests)
                                        +-- blob (test.c)

commit A --parent--> commit B --parent--> commit C ...
```

- commit 指向一个 tree（根目录）
- tree 包含多个条目，指向 blob（文件）或其他 tree（子目录）
- commit 通过 `parent` 字段链接到前一个 commit，形成有向无环图

---

## 关键技术点

### 1. 向量存储方案

采用 **方案 A（打包 blob）**：每个文件的内容和向量打包成一个 git blob。

```
Blob 格式：
[4B magic "VEC1"][4B content_len][content][4B vec_count][vec float32...]
```

### 2. 并发控制

多用户同时提交时，使用乐观锁：
- 读取当前 HEAD
- 构建 commit，设置 parent = HEAD
- 写入时比较 CAS，冲突则重试

### 3. 向量索引集成

回滚后需要触发重新索引：
```python
if reindex:
    await viking_fs.reindex(uri)
```

### 4. 与现有 VikingFS 集成

使用已有的 VikingFS API 读写文件：
- `viking_fs.ls(uri, recursive=True)` 遍历文件
- `viking_fs.read_file_bytes(uri)` 读取内容
- `viking_fs.write_file_bytes(uri, data)` 写入内容

---

## 渐进式实现路线

### Phase 1：基础快照/回滚
- `create_snapshot()`、`get_snapshot()`、`restore_snapshot()`、`copy_snapshot_to()`
- 仅支持内容版本，不包含向量

### Phase 2：向量集成
- 支持向量打包存储（`include_vectors=True`）
- 回滚时自动恢复向量
- `get_file_at_snapshot(include_vector=True)`
