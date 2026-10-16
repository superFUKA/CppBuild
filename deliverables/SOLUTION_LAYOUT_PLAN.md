# Solutionごとに1つの.cppbuildと自由な配置（2026-10-17検討）

状態：2026-10-17に§12がすべて決まり、実装した。実装中に変えた点は[設計整理メモ](DESIGN_NOTES.md)冒頭（探索で `build` に入る、`artifact_directory` の基準など）。検証結果は[実装記録](IMPLEMENTATION_STATUS.md)冒頭。以下は実装前の設計の記録。

## 1. 要望と前提

ユーザーの要望（2026-10-17）：

- `.cppbuild` は**ディレクトリのまま**、**Solutionにつき1つ**にする。Projectの管理ファイルもその中へ移す。
- 生成するCMakeファイルも `.cppbuild` 側にまとめる（前回示した案B）。ソースのディレクトリには何も書かない。
- `.cppbuild` の場所もソースの場所も自由にする。ソースの場所は `.cppbuild` に相対パスで記録する。
- 「大事な場所」（今は `.cppbuild` の親ディレクトリ）を `.cppbuild` の中で設定できるとよいかもしれない（ユーザーも迷っている）。→ §4で検討。
- ドライブをまたぐ場合はエラーでよい。
- 依存探索を、配置が自由になっても機能するようにしたい。重点的に検討する。→ §7。依頼のうち「さっき言った文面観音とか」の部分は、音声入力の誤認識とみられ、意味が取れなかった。前回の回答で依存探索について書いた内容（探索ディレクトリの下にある `.cppbuild` で見つける）を前提に、探索の範囲を広げる方向で検討した。

AGENTS.mdの「維持する条件」はすべて維持する。具体的には、生成ファイルだけでCppBuildなしに使えること（相対パス、決定的、保存しない設定を含まない）、Solutionの1ツリーでの全体／個別の共用、ビルド設定の非保存、GUIDによる依存解決、clean前の再生成禁止。§5〜§9は、これらを崩さないことを確認しながら設計した。

## 2. 現状（コードで確認）

| 前提 | 箇所 |
| --- | --- |
| Solutionの起点（`solution.root`）は `.cppbuild` の親。`Solution.open` は `.cppbuild` という名前のディレクトリだけを受け付ける | `core.py` `Solution.open`・`SolutionSettings.__init__` |
| ProjectごとにProjectのディレクトリに `.cppbuild/project.json` と `types/*.json` がある。保存するパスは各設定ファイル基準の相対パス（schema v2以降） | `core.py` `ProjectSettings`、`paths.py` |
| Projectのディレクトリは、Solutionの起点の下で互いに重ならず、起点そのものでもないこと | `SolutionSettings._validate` |
| ソース・include・PCH・出力へのコピー対象は、Projectのディレクトリの内側（`storage.contained`） | `ProjectSettings._validate`、`engine._scan` |
| 生成する `CMakeLists.txt` はSolutionとProjectの各ディレクトリに書く。リンク先Solutionの分も書く | `workspace._documents` |
| 操作ロックは `<root>/.cppbuild/operations` を、SolutionとProjectの各ディレクトリに作る | `storage.operation_lock`、`workspace.lock` |
| Projectの同一性をProjectのディレクトリ（`project.root`）で判定している | `graph.resolve`（`locations`・同じProjectの複数形式の検査・`include_external_members`） |
| 依存探索：探索ディレクトリ**直下**の `<子>/.cppbuild/project.json`（kindがsolution）だけを探す。gitのcloneは、先頭の探索ディレクトリの下の `<名前>/<記録したpath>` | `graph._dependency_directories`、`git_sources` |
| Solutionのテンプレートは、Solutionの起点の下のツリーを丸ごとコピーする | `templates._snapshot` |
| 既定の出力先は `<所有者の root>/.cppbuild/output/...` | `output_paths.root` |

## 3. 新しい配置

```text
repo/
  src/app/...                         # ソースだけ。CppBuildは何も書かない
  src/core/...
  sln/MyApp/.cppbuild/                 # Solution MyApp の設定ディレクトリ（どこに置いてもよい）
    project.json                       # Solution（kind=solution。名前は互換のため据え置き）
    CMakeLists.txt                     # 生成：入口（cmake -S repo/sln/MyApp/.cppbuild）
    CppBuildTopLevel.cmake             # 生成
    projects/App/project.json          # Project（kind=project）
    projects/App/types/executable-<hash>.json
    projects/App/CMakeLists.txt        # 生成：Projectの定義
    projects/Core/...
    templates/                         # 管理（従来どおり）
    output/  operations/               # 出力・ロック（従来どおりコミットしない）
```

- **保存するパスの基準**：
  - Solutionの `project.json` は、従来どおり自分のディレクトリ（`.cppbuild`）が基準。`projects` には、Projectのディレクトリを `"../../src/app"` のように記録する。
  - **Projectの `project.json` は、ファイルの場所ではなくProjectのディレクトリを基準にする**（従来の「各設定ファイル基準」から変更）。ファイルはもうProjectの横にないため、ファイル基準だと `../../../../src/app/src` のような値になり、`.cppbuild` を動かすたびに全Projectのファイルが変わる。Projectのディレクトリ基準なら、APIで渡す値と保存する値が同じになる。Projectのディレクトリを移しても `.cppbuild` を移しても、書き換えるのはSolutionの `projects` の1か所で済む。
- **Projectのファイルの置き場所**：`projects/<Project名>/`。GUIDではなく名前にしたのは、差分を読みやすくするため。Projectの改名は未実装なので、ディレクトリ名が変わることはない。Windowsの大文字小文字を区別しないファイルシステムで `App` と `app` が衝突するため、Project名の重複は大文字小文字を区別せずに検査する（現状は区別して検査している）。
- **生成ファイルの見分け方**：生成ファイルも `.cppbuild` の中に置くため、`file_kind` で生成ファイル（先頭行の目印）を先に判定し、それ以外を管理ファイルとする（現状は `.cppbuild` の中をすべて管理ファイルとしている）。

## 4. 「大事な場所」：基準ディレクトリ（`base_directory`）

**提案：採用する。** Solutionの `project.json` に `base_directory` を持たせ（`.cppbuild` 基準の相対パス）、既定は `".."` とする。既定のときはファイルに書かない。

基準ディレクトリの意味は、次のものに限定する。

1. APIでパスを相対で受け取るときの基準（`add_project`・`move_project`・`file_kind`・`create_file_template` など）。`solution.root` は基準ディレクトリを返す。既定なら従来と同じ値になる。
2. Solutionの範囲。テンプレートの作成とSolution全体の移設は、この下を対象にする。
3. 新しい属性 `solution.config_directory` を追加し、`.cppbuild` そのものを返す。

**Projectを置ける範囲は制限しない**（基準ディレクトリの外でもよい）。そうしないと、「どこにあっても大丈夫」という要望に反する。範囲の外にあるものは、テンプレートの作成でエラーにして一覧を示す（§8）。

例：`repo/sln/MyApp/.cppbuild` に `base_directory: "../../.."` と書くと、基準は `repo/` になる。この場合、`add_project("src/app", ...)` はリポジトリの直下から指定できる。

見送った案：

- **基準をなくし、APIを `.cppbuild` 基準にする案**：既存のAPIの意味（親ディレクトリ基準）が変わり、テンプレートの範囲も別に決める必要がある。
- **基準ディレクトリを「Projectはこの下に限る」とする案**：要望と矛盾する。

## 5. 生成するCMake（案B）

- **入口**：`.cppbuild/CMakeLists.txt`。Projectは `add_subdirectory(projects/<名前>)` で追加する。ビルドツリー内の位置は `<ツリー>/projects/<名前>` で、`engine._update` の `.vcxproj` の場所の計算もこれに合わせる。
- **Projectのファイル**：先頭で `set(_cppbuild_project_dir "${CMAKE_CURRENT_SOURCE_DIR}/<Projectのディレクトリへの相対パス>")` と `cmake_path(NORMAL_PATH ...)` を書く（CMake 3.20以降。CppBuildの最小は3.24）。現在 `${CMAKE_CURRENT_SOURCE_DIR}` でProjectのディレクトリを指している箇所は、すべてこの変数に置き換える：
  - ソースの収集
  - `target_include_directories`：相対パスはCMakeが現在のソースディレクトリ基準で解釈するため、変数を付けた絶対パスにする
  - PCH
  - 出力へのコピー
  - ビルドイベントの作業ディレクトリ
  - `VS_DEBUGGER_WORKING_DIRECTORY`
  - `gtest_discover_tests` の作業ディレクトリ
  - `source_group(TREE)`
  - C++20モジュールの `BASE_DIRS`
  - 外部依存（`CMakePackage`・`CMakeSource`・`ImportedLibrary`）のパス

  なお、`CMakeSource` は従来からビルド先を指定しているため、ソースツリーの外にあってもよい。
- **生成形式の版 `CPPBUILD_FORMAT` を1から2へ上げる**。入口の場所が変わり、リンク先Solutionは `add_subdirectory(<リンク先の.cppbuildへの相対パス> <ビルド先>)` で取り込む（ビルド先は従来から指定している）。
- **CppBuildなしでの使い方の変更**：
  - 構成：`cmake -S <どこか>/.cppbuild -B build`
  - 他のCMakeプロジェクトからの取り込み：`add_subdirectory(ECS/.cppbuild)`

  利用手順を書き直す。
- **確認済み（2026-10-17、VS2022、CMake 4.2.3）**：入口とProjectのファイルを `.cppbuild` の中に置き、ソースを `.cppbuild` の外側（別のツリーを含む）に置く構成で実験した。結果は次のとおり。
  - 構成・ビルド・実行ができる。
  - VSのフィルターがProjectのディレクトリ基準になる（`source_group(TREE)`）。
  - `LocalDebuggerWorkingDirectory` がソース側のディレクトリになる。
  - ファイルを追加すると、次のビルドで `CONFIGURE_DEPENDS` により再構成されて取り込まれる。
  - ビルドツリー（`.cppbuild/output/...`）をこのソースツリーの中に置いても問題ない。
- **未確認**：Ninja Multi-Config・VS2026での同じ構成、Linux/macOS。

## 6. 配置の規則（破綻チェックの結論）

| # | 規則 | 理由・破綻の例 |
| --- | --- | --- |
| R1 | `.cppbuild` と、Projectのディレクトリ・リンク先・探索ディレクトリ・外部依存がドライブをまたぐ場合はエラー（合意済み） | 相対パスにできない。従来と同じエラーを使う |
| R2 | Projectのディレクトリは、どの `.cppbuild` の中にも置けない。Projectのディレクトリが `.cppbuild` を含むのはよい | 中に置くと、生成ファイル・出力を走査してしまう。含む場合（たとえば `..`）は、走査が `.cppbuild` という名前のディレクトリを除くため問題ない |
| R3 | **重なりの検査は、Projectのディレクトリではなくソースディレクトリの単位で行う**。メンバーどうしのソースディレクトリは、同じ・入れ子のいずれも不可。ソースディレクトリが、自分の依存探索ディレクトリ・cloneの置き場所・`.cppbuild` 外の出力先を含むのも不可 | 1つのファイルが2つのターゲットでコンパイルされたり、cloneしたリンク先のソースが取り込まれたりする。Projectのディレクトリどうしは同じでも重なってもよくなる（全員が `..` で、ソースが `src/a`・`src/b` など）。既定の `source_directories`（`src`・`include`）のまま2つのProjectを同じディレクトリに置くと、R3でエラーになり、指定を求められる |
| R4 | 別のSolutionと同じソースディレクトリを使うのはよい（今回できるようになる）。ただし、1回のビルドに同じソースが2回入る場合は、計画を作る時点でエラーにする | リンクで両方が入ると、シンボルが重複する。CMakeのターゲット名はSolution名で区別されるため、CMake上はエラーにならず、リンク時の分かりにくい失敗になる |
| R5 | パスの比較は、Windowsでは `os.path.normcase` を通す | `resolve()` は、存在しない部分の大文字小文字を正規化しない |
| R6 | Projectの同一性は、ディレクトリではなく（設定ディレクトリ, Project名）で判定する（`graph.resolve` の `locations` などを変える） | 2つのSolutionのProjectが同じディレクトリを指せるようになるため、`project.root` では別のProjectを同じものと誤判定する |
| R7 | 操作ロックはSolutionの `.cppbuild/operations` だけにする。書き込みロックは、書くファイルがある `.cppbuild` ごとに取る | Projectのディレクトリにはもう書かない。現状でも、Projectの操作は必ずSolutionのロックを取るので、Project単位のロックは実質的に使われていない |

## 7. 依存探索（重点）

### 7.1 何が変わるか

GUIDによる解決そのもの（参照一覧、最上位優先、`MissingDependenciesError`）は変えない。変わるのは「探索ディレクトリの中のどこにSolutionがあるか」だけである。現状は `<探索ディレクトリ>/<子>/.cppbuild` の1段だけを見る。`.cppbuild` を `repo/sln/MyApp/.cppbuild` のように置けるようになると、この1段の探索では見つからない。

### 7.2 提案：再帰的な探索と、入れ子の依存の除外

1. 探索ディレクトリの下を、ディレクトリだけ幅優先でたどる。ファイルは開かない。
2. `.cppbuild/project.json` のkindがsolutionなら、そのSolutionを見つけたとする。その `.cppbuild` の中へは入らない。兄弟や子ディレクトリの探索は続ける。1つのリポジトリに複数のSolution（例：本体とサンプル）があってもよい。
3. 入らないディレクトリ：
   - 走査で除く名前（`.git`・`build` など。`SCAN_EXCLUDED` と同じ）。※実装で変更：`build` には入り、CMakeのビルドツリー（`CMakeCache.txt` があるもの）を除く（`.cppbuild` を `build/` の下に置けるように）
   - シンボリックリンク・ジャンクション
   - CppBuildの出力先（目印のファイルがあるもの）
   - clone中の一時ディレクトリ（`*.cppbuild-fetch`）
4. **見つけたSolution自身の依存探索ディレクトリとcloneの置き場所は、たどらない。** これがないと、菱形の依存（ECS→A→STL、ECS→B→STL）で、Aが同梱する `A/deps/STL` と最上位の `ECS/deps/STL` が「同じGUIDが2か所」のエラーになる。現在の「最上位の `deps/STL` が使われる」という動作（利用手順に記載）を壊す。入れ子の依存は、従来どおり最上位優先の規則で解決するので、たどる必要もない。
   - **ただし、除くのは、そのSolutionを見つけた子ディレクトリの内側にあるものに限る。** リンク先の探索ディレクトリが `..` のように外側（最上位の探索ディレクトリそのものなど）を指していると、探索全体を除いてしまうため。
5. 同じGUIDが2か所で見つかったら、従来どおりエラーにする。除外が効いているので、ここに来るのは同じSolutionを2つ置いた場合（`deps/STL` と `deps/STL-old` など）だけ。
6. 自分の `.cppbuild` が探索範囲の中にあっても、自分自身として除く（現状の「探索ディレクトリにメンバーのGUIDがある」エラーを、自分の分では出さない）。

従来の配置（`<子>/.cppbuild`）は深さ1で見つかるので、この探索は従来の上位互換になる。

### 7.3 gitのリンク

- cloneの置き場所と、`GitSource.path`（リポジトリ内の `.cppbuild` の位置）は従来どおり。`path` は従来から任意の深さを受け付けており、変更は不要。
- **追加の検査**：`link_git` とcloneの検証で、リンク先のProjectのディレクトリ・外部依存が、そのリポジトリの中にあることを確かめる。外を指すリンク先は、cloneだけではビルドできない（CppBuildなしで素のcmakeでビルドする条件が崩れる）。配置が自由になると起こりうるので、エラーにして一覧を示す。

### 7.4 費用

- 実測（2026-10-17、ウォームキャッシュ）：ディレクトリだけをたどると、18,544ディレクトリで0.84秒、216ディレクトリで0.01秒。
- 探索は依存解決のたびに走る。依存探索ディレクトリは通常cloneが数個なので、問題になりにくい見込み。
- 大きなリポジトリを置く場合に備えて、キャッシュは最初は作らず、実装後に試験で測る。必要になれば、深さの上限（例：4）を設定として足す。

### 7.5 検討して見送った案

- **リポジトリの直下に、`.cppbuild` の位置を示す目印ファイルを置く案**：`.cppbuild` の外にCppBuildのファイルが増え、「`.cppbuild` にまとめる」という要望に反する。
- **探索を1段のまま、利用者が位置を登録する案**：`link_solution` が既にこの役割を持つ。探索ディレクトリは「置けば見つかる」ための仕組みなので、自動で見つけるほうが目的に合う。

## 8. APIの変更（案）

| API | 変更 |
| --- | --- |
| `Solution.create(destination_directory, name, *, template=None, base_directory=None)` | `destination_directory` の下に `.cppbuild` を作る（従来どおり）。`base_directory` を追加 |
| `Solution.open(config_directory)` | 変更なし |
| `solution.root` / `solution.config_directory`（新規） | 基準ディレクトリ／`.cppbuild` |
| `SolutionSettingsData.base_directory`（新規） | §4 |
| `add_project(directory, name, project_type, settings=None)` | 基準ディレクトリ基準または絶対パスで指定し、外側でもよい。ディレクトリには何も書かず、まだ存在しなくてもよい。従来の「ディレクトリに `.cppbuild/project.json` があればエラー」はなくなる |
| `remove_project(name)` | `projects/<名前>/`（そのProjectの管理ファイル）を削除する。従来はProjectの横に残っていた。消えるのは設定だけで、ソースは消さない。`transaction()` で戻せる |
| `move_project(name, destination, *, auto_update=True, move_files=True)` | `move_files=False` を追加し、ファイルを動かさずに記録だけを付け替えられるようにする（gitで先に移した場合など）。`True` は従来どおりディレクトリを移す。ただし、他のSolutionと共有するソースを移すと、そちらが壊れる。これは検出できないので、資料に書く |
| `solution.move_config(destination_directory, *, keep_base=True)`（新規） | `.cppbuild` だけを移し、記録した相対パスを書き換える。`keep_base` が真なら、基準ディレクトリの実際の場所を保つ。他のSolutionからこのSolutionへの参照（`link_solution` で記録したパス）は追従しない。Solution全体を移す場合と同じ制約で、資料に書く |
| `SolutionInfo` | `config_directory` を追加 |
| `Solution.file_kind` | `.cppbuild` 内の生成ファイルを `GENERATED` にする |
| テンプレート | 基準ディレクトリを丸ごとコピーする（従来の起点と同じ扱い）。`.cppbuild`・各Projectのディレクトリ・ソースディレクトリ・Project内の参照先が基準ディレクトリの外にあれば、作成をエラーにして一覧を示す |

## 9. 移行（schema_version 5）

- **従来どおり、`open`／`reload` で自動移行する**（これまでの移行と同じ）。
- 手順：
  1. 各Projectの `<dir>/.cppbuild/project.json` と `types/` を読む。
  2. パスをProjectのディレクトリ基準へ直す。
  3. `.cppbuild/projects/<名前>/` へ書く。
  4. Solutionのファイルを版5で書く。
  5. 旧ファイルを削除する。
  - 書き込みと削除は、全部行うか何もしないか（既存の `publish_documents` に削除を足す）。
- 旧い生成ファイル（Solutionのディレクトリ・各Projectのディレクトリの、目印のある `CMakeLists.txt`・`CppBuildTopLevel.cmake`）は、目印を確かめてから削除する。目印のないファイルは触らない。
- 各Projectの旧 `.cppbuild/output`（ビルドツリー・成果物）は**削除しない**。大きく、使用中の可能性もあるため。移行の記録（ログ）に場所を出し、利用者に削除を任せる。旧 `.cppbuild` に管理ファイルが残っていなければ、依存探索はそれをSolutionとして扱わない。
- **旧形式のリンク先Solution**：§12の判断事項3。

## 10. 影響範囲

- 製品コード（ほぼ全モジュール）：
  - `core`：設定の場所、検査、`create`／`open`／`add`／`remove`
  - `paths`・`storage`：基準、ロック、削除を含む公開
  - `references`
  - `graph`：同一性、探索
  - `workspace`：生成ファイルの場所、ロック、R4
  - `cmake_files`：§5
  - `engine`：走査、ツリー内の位置、ファイル操作の保護
  - `relocation`：Projectの移動、`.cppbuild` の移動
  - `templates`
  - `file_kinds`
  - `git_sources`：§7.3
  - `output_paths`
  - `information`
  - `transactions`：復元の対象
- 試験：`.cppbuild` の配置に触れる試験が約30ファイル。移行の試験と、§6・§7の規則の試験を追加する。3生成器の実ビルドも含めて全件を再実行する。
- `examples/`、`usage_tests/` の7シナリオ。
- 資料：利用手順の冒頭（生成ファイルとCppBuildなしの使い方）・依存探索・移動、API設計、設定詳細、実装記録、AGENTS.md。

規模は、これまでで最も大きい変更（生成ファイル化の2026-10-01と同程度以上）になる見込み。

## 11. 実装の順序（案）

1. 配置と移行：Projectの設定を `.cppbuild/projects/` へ移し、Projectのディレクトリ基準で保存する。ロックはSolution単位にし、Projectの同一性を変える（R6・R7）。この時点ではProjectの場所の制限は残し、既存の全試験を配置の違いだけ直して通す。
2. 生成ファイルを `.cppbuild` へ移す（§5、FORMAT 2）。CppBuildなしの構成・ビルドを3生成器で確かめる。
3. 場所の自由化：基準ディレクトリ、Projectの場所の制限の撤廃、R2〜R5。
4. 依存探索（§7）。
5. 移動API（`move_files=False`、`move_config`）とテンプレートの範囲。
6. 資料の更新と利用シナリオ。各段階で全件の試験を回す。

## 12. 決めてほしいこと

2026-10-17ユーザー回答：1は採用（既定 `..`）。2は `.cppbuild` の中だけ（基準ディレクトリに入口を置く設定は作らない）。3は旧形式のリンク先に対応しない（読み込むと版の不一致でエラーになるだけで、変換や併存の処理は作らない）。4は削除する。5は移行を作らない（ユーザー：「互換性とか気にしなくていい」）。旧形式（schema 4以前）の読み込みと、これまでの自動移行の処理も削除し、版5だけを読む。既存のサンプル・試験・利用シナリオは新しい形式で作り直す。

1. **基準ディレクトリ（§4）を採用するか**。推奨：採用し、既定は `..` とする。
2. **入口の `CMakeLists.txt` を `.cppbuild` の中だけに置くか**。推奨：中だけに置く。素のcmakeは `cmake -S <...>/.cppbuild` で使う。VS・VS Codeの「フォルダーを開く」は、開いたフォルダーの直下の `CMakeLists.txt` を探すので、`.cppbuild` を開く必要がある。必要なら後から、基準ディレクトリに入口を呼ぶだけの小さなファイルを置く設定を足せる。
3. **旧形式（schema 4以前・FORMAT 1）のリンク先Solutionの扱い**。
   - 推奨：エラーにして、リンク先を新しいCppBuildで開いて移行・コミットするよう案内する。gitのリンクなら、移行したコミットを `set_git_source` で記録し直す。
   - もう一つの案：旧形式を読み取り専用で解釈し、生成するCMakeでもFORMAT 1の入口を取り込む。他人のリポジトリを固定版でリンクしている場合に便利だが、2つの形式を併存させる処理が増える。
   - 現在のリンク先がすべて自分のリポジトリなら、推奨案で足りる。
4. **`remove_project` でProjectの設定を削除してよいか**（§8）。推奨：削除する。
5. **移行を `open` で自動にするか**（§9）。推奨：自動にする（これまでの移行と同じ）。ただし今回は、ソース側にある旧い生成ファイルと旧 `.cppbuild` の管理ファイルも削除する。

## 13. 未検証・残る懸念

- Ninja Multi-Config・VS2026での§5の構成（VS2022だけ実験した）。
- 再帰的な探索の費用（大きなリポジトリをcloneした場合）。§7.4。
- 同じソースを2つのSolutionで使う場合、片方で `move_project(move_files=True)` を実行すると、もう片方が壊れる。これは検出できない。
- `.cppbuild` を移したときに、他のSolutionからの参照が追従しない。従来、Solution全体を移した場合と同じ。
