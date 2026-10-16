# 利用手順

<a id="solution-layout"></a>

## Solutionの配置：Solutionにつき1つの`.cppbuild`（2026-10-17）

Solutionの設定とCppBuildが生成するCMakeファイルは、すべてそのSolutionの`.cppbuild`ディレクトリにある。Projectのディレクトリにはソースだけがあり、CppBuildは何も書かない。`.cppbuild`もProjectもどこに置いてもよい（同じドライブの中）。設計と判断の経緯は[配置変更の設計](SOLUTION_LAYOUT_PLAN.md)。

```text
repo/
  src/app/...                         # Project App のディレクトリ（ソースだけ）
  src/core/...
  sln/MyApp/.cppbuild/                # Solution MyApp
    project.json                      # Solutionの設定（kind=solution）
    CMakeLists.txt                    # 生成：入口（cmake -S repo/sln/MyApp/.cppbuild）
    CppBuildTopLevel.cmake            # 生成
    projects/App/project.json         # Project Appの設定
    projects/App/types/*.json         # 種類別の設定
    projects/App/CMakeLists.txt       # 生成：Projectの定義
    templates/                        # 共有素材
    output/  operations/              # ビルドツリーとロック（コミットしない）
```

```python
from cppbuild import ProjectType, Solution

# repo/sln/MyApp/.cppbuild を作る。基準ディレクトリ（APIに渡す相対パスの基準）はリポジトリのルート
solution = Solution.create("repo/sln/MyApp", "MyApp", base_directory="../..")
core = solution.add_project("src/core", "Core", ProjectType.STATIC_LIBRARY)   # repo/src/core
app = solution.add_project("src/app", "App", ProjectType.EXECUTABLE)
vendor = solution.add_project("D:/work/vendor", "Vendor", ProjectType.STATIC_LIBRARY)  # 絶対パス（同じドライブ）も可

solution.root               # 基準ディレクトリ（repo）
solution.config_directory   # repo/sln/MyApp/.cppbuild
solution.set_base_directory("src")          # 基準だけを変える（何も動かさない）
solution.move_config("build/solutions/MyApp")  # .cppbuild を移す（記録したパスの指す先は変わらない）
solution.move_project("App", "apps/app")       # Projectのディレクトリをファイルごと移す
solution.move_project("Core", "libs/core", move_files=False)  # git mv 等で移した後、記録だけ付け替える
```

- **基準ディレクトリ**（`base_directory`、既定は`.cppbuild`の親）：APIに相対パスで渡すときの基準（`add_project`・`move_project`・`file_kind`・`create_file_template` など）と、テンプレートでコピーする範囲。`Solution.create(..., base_directory=)` で`.cppbuild`を作る場所からの相対（または絶対）で指定し、Solutionの`project.json`に`.cppbuild`からの相対で保存する（既定のときは書かない）。Projectを置ける範囲は制限しない。
- **保存するパス**：Solutionの`project.json`は`.cppbuild`からの相対、Projectの`project.json`・種類別の設定はProjectのディレクトリからの相対（APIで渡す値と同じ）。`.cppbuild`を動かしても、Projectを動かしても、書き換わるのはSolutionの記録だけ。ドライブをまたぐパスは相対にできないため、エラー。
- **生成ファイル**：入口は`.cppbuild/CMakeLists.txt`。Projectのファイル（`.cppbuild/projects/<名前>/CMakeLists.txt`）は、先頭で自分の位置からProjectのディレクトリを求め（`_cppbuild_project_dir`）、ソース・include・作業ディレクトリ等をそこから指す。CppBuildなしで使うときは `cmake -S <...>/.cppbuild -B <ビルド先>`。VS・VS Codeの「フォルダーを開く」では`.cppbuild`を開く。生成形式の版（`CPPBUILD_FORMAT`）は2。
- **配置の規則**：
  - Projectのディレクトリは、どの`.cppbuild`の中にも置けない。Projectのディレクトリが`.cppbuild`を含むのはよい（例：`add_project(".", ...)` で基準ディレクトリそのものをProjectにし、`source_directories = ["."]`）。走査は`.cppbuild`という名前のディレクトリに入らない。
  - 1つのファイルは1つのProjectに属する。同じSolutionのProjectどうしでソースディレクトリ（`source_directories`）が同じか入れ子なら、保存時にエラー。Projectのディレクトリが同じでも、ソースディレクトリが分かれていればよい（既定の `["src", "include"]` のまま同じディレクトリに2つ置くとエラーになるので、指定する）。
  - 依存探索ディレクトリとソースディレクトリが重なるのもエラー（cloneしたリンク先をソースとして集めてしまうため）。
  - 別のSolutionと同じディレクトリをProjectにしてよい（例：`sln/App`と`sln/Tools`が同じ`src/core`を使う）。ただし両方が1回のビルドに入る場合（リンクで取り込む場合）は、同じソースを2回コンパイルすることになるので、計画の時点でエラー。
  - Project名は大文字小文字の違いだけでは区別しない（`projects/<名前>`が衝突するため）。
- **`remove_project`**：Projectの設定（`.cppbuild/projects/<名前>/`）を削除する。Projectのディレクトリのファイルは残す。`transaction()` の中なら戻せる。
- **`move_config(destination, *, keep_base=True)`**：`.cppbuild`の管理ファイルを`destination/.cppbuild`へ移し、記録したパスを書き直す。生成ファイルは新しい場所で次の操作のときに書き直す。`output/`（ビルドツリー）と`operations/`は元の場所に残すので、不要なら手で消す。`keep_base=False`なら基準ディレクトリも`.cppbuild`と一緒に動く。ほかのSolutionからこのSolutionへの記録（`link_solution`の参照）は追従しない。
- **`move_project(..., move_files=True)`**：Projectのディレクトリを移す（移動先は基準ディレクトリからの相対か絶対で、まだ存在しない場所）。Projectの設定は`.cppbuild`に残る。ディレクトリがまだない（ファイルを1つも作っていない）Projectは記録だけを移す。`.cppbuild`を含むディレクトリは移せないので、自分で移して`move_files=False`を使う。同じディレクトリを使う別のSolutionは追従しない。
- **テンプレート**：基準ディレクトリのツリーを丸ごとコピーし、`.cppbuild`はツリー内の同じ位置に置く。`.cppbuild`と全Projectが基準ディレクトリの中にないとエラー。`create_solution_template` は新しいテンプレートの`.cppbuild`を返し、`Solution.create(destination, name, template=...)` はテンプレートの基準ディレクトリの中身を`destination`に展開する。
- **旧形式**：以前の形式（Projectごとに`.cppbuild`があった schema 4 以前）は読まない（`Unsupported schema` のエラー）。移行の処理はない。新しいCppBuildで作り直す。

<a id="generated-cmake"></a>

## 生成するCMakeファイルとCppBuildなしでのビルド（2026-10-01、10-17更新）

CppBuildは、Solutionとそのリンク先を1つのCMakeプロジェクトとして生成する。生成したファイルは`.cppbuild`の中に置き、コミットして配布できる。CppBuild・Python・`.cppbuild`のJSONがなくても、CMakeだけで構成・ビルド・.sln生成・実行・テストができる。

```text
ECS/.cppbuild/                     # Solution ECS
  CMakeLists.txt                   # 生成：入口。所属Projectを add_subdirectory する
  CppBuildTopLevel.cmake           # 生成：ECSを最上位にしたときの既定値とリンク先Solution
  projects/App/CMakeLists.txt      # 生成：Projectの定義（ディレクトリ・ソースディレクトリ・include・定義・リンク）
  projects/Core/CMakeLists.txt
ECS/deps/STL/.cppbuild/CMakeLists.txt                  # リンク先Solution（STL自身の生成ファイル）
ECS/deps/STL/.cppbuild/projects/Containers/CMakeLists.txt
```

CppBuildなしで使う例（生成器・コンパイラ・アーキテクチャは利用者が選ぶ）：

```powershell
cmake -S ECS/.cppbuild -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
ctest --test-dir build -C Release
build/bin/Release/App.exe
```

- **生成のタイミング**：update・build・rebuild・run・testのたびに、操作するSolutionの生成ファイルを書き直す（内容が同じなら書き換えない）。clean・設定の保存だけでは生成しない。ファイルの追加・設定変更の後は、これらの操作で反映する。
- **生成ファイルの性質**：先頭行が `# Generated by CppBuild` で、手で編集しない。パスはすべて相対パス。内容は管理JSONだけで決まり（2026-10-12更新：ソースファイルの一覧は含まない）、生成器・コンパイラ・アーキテクチャ・構成などの保存しない設定や、PCごとのパスを含まない。同じ入力からは毎回同じ内容になる。
- **ソースファイルの収集**（2026-10-12更新）：生成ファイルにはソースファイルを1つずつ書かず、Projectの `source_directories` だけを書く。ファイルはCMakeが構成のたびに集め（Solutionの `CMakeLists.txt` の `_cppbuild_collect_sources`）、ファイルの追加・削除・改名では生成ファイルは変わらない。`CONFIGURE_DEPENDS` により、CppBuildなしの `cmake --build`（VSのビルドを含む）でも、次のビルドで追加・削除に気づいて構成し直す。集める範囲はCppBuildの走査と同じで、拡張子に関係なくすべてのファイル（隠しファイルを含む）を集め、`.cppbuild`・`.git`・`build`・`__pycache__`・`.cache`・`.venv` という名前のディレクトリ、CppBuildの出力先、シンボリックリンク・ジャンクション、CppBuildが生成した `CMakeLists.txt`・`CppBuildTopLevel.cmake` は除く。`.cpp`・`.cc`・`.cxx`（大文字小文字を問わない）だけをコンパイルし、ほかはVSに表示するだけ。フィルタはディレクトリ構成から作る（`source_group(TREE)`）。名前に `;` を含むファイルはCMakeのリストで扱えないため、従来どおりエラー。何も変わっていないビルドでも毎回ディレクトリを確認するため、実測で1回あたりNinjaで約0.02〜0.03秒、VS2022で約0.06〜0.08秒増える（2,001ファイル・102ディレクトリでも同程度）。VSで開いたまま追加してビルドすると、構成し直した後にプロジェクトの再読み込みを求められることがある。
- **既存ファイルの保護**：`.cppbuild`の生成ファイルの場所に、CppBuildが生成していない `CMakeLists.txt` があると、上書きせずエラーにする。2026-10-17更新：Projectのディレクトリには生成しないので、そこに自分の `CMakeLists.txt` を置いてもよい（ほかのファイルと同じく集められる）。2026-10-03更新：書き込む前に全ファイルを確認し、1つでも該当があれば何も書かない（エラーに該当パスをすべて示す）。書き込み途中で失敗した場合は、書き換えたファイルを元に戻す。ソース走査・テンプレートの対象からも除外する。
- **ターゲット名**：`<Solution名>_<Project名>`（例 `ECS_App`）。成果物のファイル名はProject名のまま（`App.exe`、`Core.lib`）。手書きのCMakeからは別名 `<Solution名>::<Project名>`（例 `STL::Containers`）でリンクできる。CppBuild同士のリンクは `<Solution名>::<Project名>_<形式>`（例 `STL::Containers_static`）を使う。別のSolution・Projectが同じターゲット名になる場合や、同名の共有ライブラリが2つある場合はエラー。2026-10-03更新：検査は、CppBuildなしで切り替えられる形式も含めたすべてのターゲット名（例：共有ライブラリ `Lib` の静的版 `S_Lib_static` と、Project `Lib_static`）と別名に広げた。CMake・GoogleTestが定義する名前（`ZERO_CHECK`、`gtest_main` など）になる場合もエラー。実行ファイル・TESTと同名の共有ライブラリを一緒にビルドする場合も、`bin/<構成>` の `App.pdb`・`App.ilk` を上書きし合うためエラー（IDE表示だけのものは出力先が別なので対象外）。
- **最上位での既定値**（`CppBuildTopLevel.cmake`、`-D` で上書き可）：単一構成の生成器では `CMAKE_BUILD_TYPE=Debug`、`CMAKE_CXX_STANDARD=20`、実行ファイルとDLLは `<ビルドディレクトリ>/bin/<構成>` にまとめて出す（DLLが実行ファイルの横に置かれる）。`enable_testing()` も行う。
- **Visual Studioの起動対象**（F5で開始するProject）：Solutionの `CMakeLists.txt` が `VS_STARTUP_PROJECT` を設定する。候補の順は、`main_project` が実行ファイル・TESTならそれ、次に実行ファイル（Project名順）、次にTEST（Project名順）で、そのうち構成されたターゲットの最初を選ぶ（`BUILD_TESTING=OFF` ならTESTは飛ばす）。実行できるProjectがなければ設定しない。そのSolutionの .sln／.slnx だけに効き、リンク先Solutionのプログラムは候補にしない。従来は設定がなく、.slnの先頭の ALL_BUILD が起動対象になって「プログラムを開始できません」になっていた。起動対象はVS上で変更でき、その選択はVSの `.vs` に保存されるため、以後の再生成では変わらない。2026-10-12追加。同日追加：実行ファイル・TESTのデバッグ時の作業ディレクトリ（`VS_DEBUGGER_WORKING_DIRECTORY`）をProjectのディレクトリにする。CppBuildの `run` とテストの探索と同じ場所で、相対パスでファイルを読むプログラムがF5でも同じように動く（従来はVSの既定でビルドツリー側のフォルダーだった）。`run` が `artifact_directory` のDLLのためにPATHへ足すディレクトリは、保存しない設定で決まるため生成ファイルには入れない。
- **リンク先Solution**：最上位Solutionが解決したリンク先（参照一覧・依存探索ディレクトリ・最上位優先の規則は従来どおり）を `add_subdirectory` で取り込み、そのうち必要なProjectだけを構成する。リンク先のProjectは、リンク先自身の生成ファイルで定義される。リンク先Solutionを丸ごと移動した場合は、利用側で再生成が必要（相対配置を保つ移設は不要）。リンク先のテストは、そのSolutionが最上位のときだけ登録する。2026-10-03更新：CTestのテスト名は `<Project名>.<スイート>.<テスト>`（GoogleTestの `TEST_PREFIX`）。2つのTEST Projectに同名のテストがあっても、それぞれのラベルで自分のテストだけを実行する。CppBuildの `TestCaseResult.name` は従来どおり接頭辞なし（`Suite.Test`）。
- **他のCMakeプロジェクトからの利用**：CppBuildを使わないプロジェクトが `add_subdirectory(ECS)` すると、ECSの全Projectとリンク先が追加される（既定値の設定とテストの登録は最上位のときだけ）。CppBuild同士の取り込みでは、必要なProjectだけを追加する。
- **CppBuildでの操作との関係**：CppBuildも同じファイルで構成する。保存しない設定は、ビルドツリー内の `cppbuild-cache.cmake` を `cmake -C` で渡す。リンク先Solutionの生成ファイルも、取り込むProjectの分だけ書き直す。リンク先自身の依存が解決できれば、その `CppBuildTopLevel.cmake` と残りのProjectも書き直す。
- **CMakeのキャッシュ変数**（CppBuildなしで使う場合の切り替え）：

| 変数 | 意味 |
| --- | --- |
| `BUILD_SHARED_LIBS` | CMake標準の切り替え。ONなら、形式を個別に選んでいない静的／共有ライブラリを `SHARED` とする（CppBuildはOFFを渡す） |
| `<Solution>_<Project>_TYPE` | そのライブラリだけの形式（`BUILD_SHARED_LIBS` より優先）。空（既定）は作成時形式で、各リンクは保存した形式のまま。`STATIC`／`SHARED` はその形式に加え、静的・共有のリンクをすべてその形式にする。`INTERFACE` はヘッダーとして使う |
| `<Solution>_<Project>_CXX_STANDARD` | そのProjectだけのC++標準（未定義なら `CMAKE_CXX_STANDARD`） |
| `<Solution>_<Project>_OUTPUT_DIRECTORY` | そのProjectの成果物の出力先（空なら既定の配置） |
| `CPPBUILD_GOOGLETEST_URL` | GoogleTest 1.14.0のURLまたはローカルZIP（SHA256は固定で検証） |
| `BUILD_TESTING` | CTestの標準の切り替え（既定ON）。OFFならTEST Projectを構成せず、GoogleTestも取得しない |

GoogleTestの `BUILD_GMOCK`（2026-10-12からON）・`INSTALL_GTEST`・`gtest_force_shared_crt` は通常の変数で既定値を与えるだけで、利用者が `-D` で指定した値を上書きしない。実行ファイルのinclude・定義・リンクは `PRIVATE`。

<a id="output-directories"></a>

## ビルドツリーと成果物の出力先（2026-10-01更新）

```python
from cppbuild import ProjectBuildSettings, SolutionBuildSettings

solution.set_build_settings(SolutionBuildSettings(intermediate_directory="output/intermediate"))
project.set_build_settings(ProjectBuildSettings(artifact_directory="D:/CppBuild/products"))
```

- **ビルドツリー**：Solutionごと・生成環境（生成器・アーキテクチャ・ツールセット・コンパイラ・ツールチェーン）ごとに1つ。`SolutionBuildSettings.intermediate_directory`（既定 `output/intermediate`、Solutionの`.cppbuild`基準）の下の10桁の識別子のディレクトリ。Debug／Release等は同じツリーで扱う。ライブラリの形式もツリーを分けない。
- **全体と個別**：個別操作（Project.update/build/run/test/clean/rebuild）も同じツリーで、そのProjectのターゲットだけをビルドする。CMakeが依存先を必要に応じてビルドし、全体と個別で同じ成果物を使う。構成（configure）は常にSolution全体で行うため、他ProjectのCMakeの誤りで個別操作も失敗する。
- **Projectの個別の生成環境**：Projectに別の生成器・アーキテクチャ等を指定すると、その環境のSolutionツリーで全体を構成し、そのProjectをビルドする。
- **成果物の出力先**：`ProjectBuildSettings.artifact_directory`（既定 `None`）。`None` ではCMakeの既定の配置（実行ファイル・DLLは `<ツリー>/bin/<構成>`、静的ライブラリ等は各Projectのビルドディレクトリ）。指定するとそのProjectの成果物を `<指定先>/<識別子>/<構成>` に出す。相対パスはSolutionの`.cppbuild`基準（2026-10-17更新：以前はProjectの`.cppbuild`基準）。DLLを別の場所に出した場合でも、CppBuildのrun・testは`PATH`にそのディレクトリを加える。
- **廃止**：`ProjectBuildSettings.intermediate_directory` はなくなった（渡すと `TypeError`）。Projectごとの`.cppbuild/output`は使わない。
- どちらも非保存で、再open後は既定値に戻る。set_build_settingsは設定全体の置き換え。
- 所有マーカーのない既存ディレクトリや、別のSolution・生成環境の所有マーカーがあるツリーは使わずにエラーにする。出力領域はソース走査・テンプレートから除外する。
- 出力先・ツリーを変えても旧生成物は削除しない。WindowsではCMake内部にも長いパスができるため、深いソース配置では短い絶対パスのツリーを指定する。目安はビルドツリーのパスがVSで約130文字、Ninjaで約155文字まで（長いパスを有効にしないWindowsでの実測）。これを超えるツリーで構成・ビルドが失敗すると、結果の出力の末尾に `Hint: the build tree path has N characters ...` の案内が付く。

Windows、Python 3.11以上、Visual Studio 2022のC++ツールとWindows SDK、CMake/CTest 3.24以上を前提とする。実検証した環境は[実装記録](IMPLEMENTATION_STATUS.md)を参照。

リポジトリのルートで実行する。作成先には新しいディレクトリを指定する。

```powershell
python -m examples.google_test .test-work/my-google-test
python -m examples.complete_workflow .test-work/my-workflow
```

初回のTEST構成時にGoogleTest 1.14.0をGitHubから取得し、固定SHA256を検証する。外部通信が許可されない環境では同じ版のZIPを第2引数で指定する。

```powershell
python -m examples.google_test .test-work/my-offline-test C:/archives/v1.14.0.zip
```

`google_test`は作成・ビルド・CTest実行、`complete_workflow`は共有素材、イベントによるテスト追加、環境診断、全体ビルド・テスト、テンプレート作成・復元後の再テストを行う。

<a id="logging"></a>

## 詳細ログ（2026-10-16）

CppBuildが行った操作を、AIが読んで問題を調べられるくらい細かく記録する。既定は無効で、設定は保存しない（プロセスごとに指定する）。

```python
import cppbuild
from cppbuild import LogSettings

cppbuild.configure_logging(LogSettings(level="DEBUG", file="logs/cppbuild.log"))   # 有効にする
cppbuild.configure_logging(LogSettings(level="TRACE", file="logs/cppbuild.jsonl", format="json"))
cppbuild.configure_logging(None)            # 無効にする（LogSettings(enabled=False) も同じ）
cppbuild.log_settings()                     # 今の設定（file は絶対パス）
```

| 項目 | 既定 | 内容 |
| --- | --- | --- |
| `enabled` | True | Falseで無効 |
| `level` | `"DEBUG"` | `ERROR`・`WARNING`・`INFO`・`DEBUG`・`TRACE`（大文字小文字を問わない。一覧は `cppbuild.LOG_LEVELS`） |
| `file` | None | 書き出すファイル。相対パスは設定した時点の作業ディレクトリ基準。ディレクトリがなければ作る。UTF-8 |
| `console` | None | 標準エラーにも出す。Noneは `file` がないときだけ出す |
| `format` | `"text"` | `"text"`（1行1件）か `"json"`（JSON Lines。`time`・`level`・`logger`・`thread`・`call`・`depth`・`message`、あれば `data`・`exception`） |
| `append` | True | Falseなら設定時にファイルを空にする |

レベルごとに記録するもの（`DEBUG` は `INFO` の分も、`TRACE` は両方の分も含む）：

- `INFO`：利用者が呼んだ公開API（`Solution`・`Project`・設定・`TemplateTools`・`Environment`）の開始・終了と所要時間、実行したコマンド（CMake・CTest・MSBuild・Ninja・git・vswhere・vcvarsall・作ったプログラム）と終了コード、書いた・消した・移動したファイル、トランザクションの開始・確定・巻き戻し。失敗したコマンドは `WARNING`、利用者へ届く例外は `ERROR`（トレースバック付き）。
- `DEBUG`：公開APIの引数と戻り値、CppBuildの中から呼ばれた公開API（`(in #3)` のように呼び出し元の番号付き）、コマンドの出力全部・作業ディレクトリ・このプロセスと違う環境変数、構成を省いた・省かなかった理由、ロックの取得と解放、イベントの通知とコールバックの例外、読んだJSONのパス。
- `TRACE`：コマンドの環境変数すべて、書いたファイルと読んだJSONの中身。

各行の `#12` は公開APIの呼び出しの番号で、その呼び出しの中で起きたことに同じ番号が付く。名前に `TOKEN`・`SECRET`・`PASSWORD`・`CREDENTIAL`・`API_KEY`・`PRIVATE_KEY`・`AUTH` を含む環境変数の値と、URLの中のパスワードは `***` に置き換える。それ以外（パス、ソースの中身を含むコマンドの出力など）はそのまま書くので、ログを人に渡すときは中身を確認する。

コードを変えずに有効にするには、`cppbuild` を読み込む前に環境変数を設定する：

```powershell
$env:CPPBUILD_LOG = "TRACE"            # 1/true/on はDEBUG、0/false/off は無効
$env:CPPBUILD_LOG_FILE = "cppbuild.log" # なければ標準エラー
$env:CPPBUILD_LOG_FORMAT = "json"       # 省略時 text
python my_script.py
```

ログはPython標準の `logging` のロガー `cppbuild`（子に `cppbuild.api`・`cppbuild.process`・`cppbuild.files`・`cppbuild.storage`・`cppbuild.engine`・`cppbuild.events`・`cppbuild.transactions`）へ送る。`TRACE` の値は `cppbuild.LOG_TRACE`（5）。利用側のプログラムが自分でハンドラーを付けてもよい。`configure_logging` はCppBuildが付けたハンドラーだけを入れ替え、無効にするとロガーのレベルを未設定へ戻す（その場合、利用側のloggingの設定に従う）。

- `TRACE` は大きくなる（実測：Project1つの最初のビルドで `DEBUG` 約22KB、`TRACE` 約137KB）。通常の調査は `DEBUG` で足りる。
- `run(wait=False)` のように別スレッドで動くコマンドは、呼び出し番号が付かず `-` になる（スレッド名で区別する）。

## Pythonからの利用

```python
from cppbuild import ProjectType, Solution

solution = Solution.create("MySolution", "MySolution")
tests = solution.add_project("Tests", "Tests", ProjectType.TEST)
tests.add_file(
    "src/example.cpp",
    content='#include <gtest/gtest.h>\nTEST(Example, Value) { EXPECT_EQ(2 + 2, 4); }\n',
    auto_update=False,
)
report = tests.test()
for case in report.cases:
    print(case.name, case.status)
if not report.success:
    for process in report.processes:
        if not process.success:
            print(process.output)
    print(report.diagnostics)
```

オフラインZIPやツールパスは非保存のビルド設定で渡す。再open後は設定し直す。`set_build_settings`は差分更新ではなく全置換する。

```python
from cppbuild import ProjectBuildSettings, SolutionBuildSettings, ToolSettings

solution.set_build_settings(SolutionBuildSettings(
    tools=ToolSettings(cmake="C:/Program Files/CMake/bin/cmake.exe",
                       ctest="C:/Program Files/CMake/bin/ctest.exe"),
    test_projects=["Tests"],
))
tests.set_build_settings(ProjectBuildSettings(
    googletest_archive="C:/archives/v1.14.0.zip",
    test_parallel=2,
))
```

`solution.check_environment()`は選択ツールでC++の構成・ビルドを行い、不足情報を返す。GoogleTestのオンライン接続性はこの診断で確認せず、初回のupdate/build/testで確認する。通信失敗時はProcessReport.outputに取得URLとCMakeのエラーが残る。

## Projectの移動

```python
report = solution.move_project("Library", "libraries/Library")
if not report.success:
    print(report.update_error)
    if report.update is not None:
        for process in report.update.processes:
            print(process.output)

# CMakeへの反映を後でまとめる場合
report = solution.move_project("App", "applications/App", auto_update=False)
updated = solution.update()
```

移動先は基準ディレクトリからの相対（または絶対）で、まだ存在しないフォルダーを指定する。2026-10-17更新：基準ディレクトリの外へも移せる。実フォルダーと登録先を一緒に移し、内部依存・既存Projectオブジェクト・非保存設定を保持する。Projectの設定と生成ファイルは`.cppbuild`にあり、動かない（Projectのファイルの位置を指す相対パスだけを書き直す）。`move_files=False`で記録だけを付け替えられる（[Solutionの配置](#solution-layout)）。既定ではSolutionのCMakeファイルを再生成して構成し直すが、コンパイルはしない。必要に応じてbuild/run/testを実行する。

2026-10-01更新：ビルドツリーはSolutionごとなので、移動したProjectのキャッシュは退避しない（旧配置のビルドディレクトリはツリー内に残る）。2026-10-17更新：Projectのディレクトリにキャッシュはないので、退避（`.cppbuild/relocations`）もなくなった。再生成失敗時も移動自体は完了しているため、原因を直してsolution.updateを再実行する。移動前から実フォルダーが存在しない場合の登録修復には対応しない。

他のビルド・設定操作を停止し、非同期runはwaitしてから呼び出す。他Solutionの利用側は、移動したSolutionの入口を取り込むため、Solution内の移動では再生成が不要。利用スクリプトやC++本文、任意の引数・環境変数に埋め込んだパスは必要に応じて修正する。リンクやジャンクションを含む移動は拒否する。

## 自動テスト

```powershell
python -m unittest discover -s tests -v
```

通常実行では実VS2022試験をskipする。実ビルドを含める場合は次を設定する。

```powershell
$env:CPPBUILD_TEST_VS2022 = '1'
$env:CPPBUILD_TEST_GTEST_ARCHIVE = 'C:/archives/v1.14.0.zip'
$env:CPPBUILD_TEST_NINJA = '1'    # Ninja Multi-Config（WindowsではMSVCをvcvarsallで自動準備）
$env:CPPBUILD_TEST_VS2026 = '1'   # Visual Studio 18 2026（.slnx）
python -m unittest discover -s tests -v
```

APIの結果型・イベント規則・テンプレートの除外範囲は[API設計](API_DESIGN.md)、保存境界は[設定詳細](SETTINGS_DESIGN.md)を参照。
# ソリューションフォルダー（2026-09-29追加）

全体 `.sln` の表示階層を保存設定で指定できる。実ディレクトリやProjectの所属は変更しない。

```python
from cppbuild import SolutionFolderSettings

settings = solution.settings.get()
settings.solution_folders = SolutionFolderSettings(
    projects="Projects",
    linked_projects="LinkedProjects",
    project_folders={"App": "Apps/Tools", "Core": "Libraries"},
    external="External",  # 省略時も External
)
solution.settings.save(settings)
report = solution.update()
```

`project_folders` のキーは自分の所属Project名。値は `Projects` 配下の `/` 区切り相対階層で、未指定・空文字列なら直下。絶対パス・`..`・外部Project名は拒否する。ルートの三つのフォルダー名（`projects`・`linked_projects`・`external`）は単一階層で、互いに同名にできない。

`external` には、CppBuildが書いたのではないターゲットを、作ったものごとに分けて置く。`External/CMake` にCMakeが作るもの（ALL_BUILD・ZERO_CHECK・RUN_TESTS等）、`External/GoogleTest` にGoogleTestの `gtest`・`gtest_main`。リンク先SolutionのTESTが取り込むGoogleTestも、最上位Solutionのこのフォルダーへ入る（リンク先の設定は使わない）。素のcmakeでは `CppBuildTopLevel.cmake` の `CPPBUILD_EXTERNAL_FOLDER` と `PREDEFINED_TARGETS_FOLDER` で決まる（最上位として構成したときだけ。CppBuildを使わない親プロジェクトに加えられたときは、全体の設定であるCMakeの既定ターゲットの置き場所を変えない）。2026-10-11追加。

リンク先は `LinkedProjects/<相手のSolution名>` に全所属Projectを表示する。間接リンク先も同じ階層へ集約し、同じSolutionは重複表示しない。同名の別Solutionはパス由来の短い識別子を表示名へ付ける。表示用に追加した未参照Projectは通常のビルド対象に加えない（CMakeの `EXCLUDE_FROM_ALL`／`EXCLUDE_FROM_DEFAULT_BUILD`）が、表示する `.vcxproj` の生成には、そのProjectの有効な設定・ソース・外部依存が必要になる。TESTの構成ではGoogleTestの取得が必要になり得る。2026-10-01更新：フォルダーはCMakeの `FOLDER`（`CMAKE_FOLDER`）で表す。生成ファイルは生成器に依存しないため、Ninjaでも未参照Projectを構成する（ビルドはしない）。2026-10-03更新：未参照Projectが必要とする依存先の形式も、通常のビルド対象に加えない（実際の依存で使う形式だけを作る）。未参照Projectの実行ファイル・DLLは `bin/_linked/<リンク先Solution>/<構成>` に出し、利用側の同名実行ファイル（例：両方のSolutionの `App`）と衝突させない。同名の共有ライブラリ（DLL名の衝突）は、表示用でも従来どおりエラーにする。

未参照Projectは明示選択、または作成時形式で表示する。選択可能なライブラリ3形式をすべて表示するための生成は行わない。実際の依存で参照済みのProjectは要求された種類を表示する。全体操作の構成・architecture・ToolSettingsの整合条件は表示用Projectにも適用する。外部Solutionの設定は `external_build_settings` で指定できる。

保存・再open・テンプレート復元で表示設定を保持する。Project登録解除時は対応する配置設定も削除する。`settings.solution_folders = None` を保存してupdateすると、依存Projectのみの階層なし表示へ戻る。2026-10-01更新：.slnはSolutionに1つなので、個別Projectのupdateでも表示に反映される。

# GUIDによるリンクの自動管理（2026-09-30更新）

```python
from cppbuild import ProjectType

# 呼び出すだけで、対象ProjectのGUIDを所属Solutionに登録する。
a = app_a.settings.link_solution(external_config, ProjectType.STATIC_LIBRARY)
b = app_b.settings.link_solution(external_config, ProjectType.STATIC_LIBRARY)
references = solution.settings.get().references  # 参照は1件、利用元は2つ

target_guid = app_a.settings.get().dependencies[a.dependency_id].project_guid
target_config = references[target_guid].solution_directory
app_a.settings.unlink(a.dependency_id)  # app_bが使用中なので登録を保持
app_b.settings.unlink(b.dependency_id)  # 最後の利用なので登録を自動削除
```

名前指定は廃止したため、`name=` を渡していた呼び出しは引数を削除する。同じ対象GUID・同所在は共用し、同じGUIDの異なる所在はエラー。ただし、ECSが `deps/STL` と `deps/A` をリンクし、Aが自分の参照一覧で同じGUIDのSTLを `A/deps/STL` として登録している場合、ECSのビルドではECSの `deps/STL` を使い、Aの単独ビルドでは `A/deps/STL` を使う。このように、操作を始めた最上位Solutionの参照一覧に登録された所在が優先される。最上位が登録していない同じGUIDのコピーが複数あれば従来どおりエラー。同じProjectへ同GUID・同種類を二度追加することはできない。ビルド・全体.slnでもGUIDと種類に基づき一つに集約する。単一の利用Projectから同じ対象の異なる種類をリンクすることは従来どおり不可。

GUIDは `project.settings.get().guid` で取得できる。`add_project` は渡した設定のGUIDを引き継がず、新しいGUIDを発行する。Project移動はGUIDを保持するので、既存リンクは移動後も同じProjectを参照する。テンプレートから新規作成した所属Projectは新しいGUIDになるが、外部への参照は維持する。

Projectの登録解除でも未使用になった参照を自動削除する。依存の変更を生成物へ反映する際は、従来どおりupdate/buildを呼ぶ。リンク先のファイルや成果物は削除しない。

2026-10-17更新：旧管理ファイル（schema 4以前）の自動移行はなくなった（読み込むとエラー）。利用側と外部Solutionの相対配置を変えた場合は、全利用元でunlinkして新しい所在へ再リンクする。相対配置を維持した一括移設では設定変更は不要。GUIDだけで移動先を探索する機能はない。

# 別の環境への移設と相対パス（2026-09-30追加）

2026-10-17更新：Solutionの管理JSONのパスは`.cppbuild`からの相対、Projectの管理JSON（`.cppbuild/projects/<名前>/`）のパスはProjectのディレクトリからの相対で保存する。APIへ絶対パスを渡しても保存時に変換する。APIに相対パスを渡す場合は、Solution設定が基準ディレクトリ（`solution.root`）、Project設定・リンク先がProjectのディレクトリ基準。open/createの引数は作業ディレクトリ基準のまま。

例えば次の配置では、Consumerの参照一覧に `../../Provider/.cppbuild` を保存する。

```text
Workspace/
  Consumer/.cppbuild/project.json
  Provider/.cppbuild/project.json
```

Workspace全体を別の場所へ移してから `Solution.open(new_workspace / "Consumer/.cppbuild")` すれば、移設先のProviderを参照する。ソース、include、PCH、共有素材、CMakeソース／パッケージ、ImportedLibraryのDLL・LIB・includeも同じ方式で保存する。利用側と依存先の相対配置を維持すること。

- 既存CMakeキャッシュは移設先で再利用せず、update/buildして再生成する。新配置では所在を含む識別子が変わるため旧領域を参照しない。テンプレート機能は出力領域を除外する。
- ツールの所在など非保存ビルド設定は移設先で設定し直す。定義文字列・任意引数・外部CMakeListsやソース本文に埋め込まれた絶対パスは自動変換しない。
- 別ドライブや別共有など相対パスにできない参照はエラー。設定と依存先を共通のドライブ／共有配下へ配置する。

# クリーン（2026-10-01更新）

```python
report = project.clean()   # そのProjectの、現在の構成の成果物を削除
report = solution.clean()  # build_projects未指定ならツリー全体、指定ならそのProjectだけ
for process in report.processes:
    print(process.output)
```

- 対象はSolutionのビルドツリー（生成環境ごと）のうち、現在の構成。事前のCMake構成・再生成・ソース走査・依存解決は行わない。ソースがなくなった場合や外部Solutionがオフラインの場合もcleanできる。
- **Projectのclean**：そのProjectの成果物と中間ファイルだけを削除し、依存先・他Projectの成果物は残す。VSはそのProjectの.vcxprojへMSBuildのClean（`BuildProjectReferences=false`）を直接実行する、Ninjaは標準の手段がないため、File APIが示すそのターゲットの成果物とオブジェクトディレクトリを削除する。ツリーにそのターゲットがなければ「Nothing to clean」で成功。
- **Solutionのclean**：`build_projects` を指定しなければツリーの現在の構成を丸ごと削除する（リンク先・GoogleTest・CMakeSourceの成果物を含む）。VSは `ALL_BUILD.vcxproj` へのMSBuildのClean、Ninjaは `ninja -t clean`。
- **再構成しない**：生成ファイルが変わってもcleanでCMakeが再構成されないよう、`cmake --build`（VSではZERO_CHECKを先にビルドする場合がある）や `clean` ターゲット（Ninjaではbuild.ninjaを作り直す）を使わない。CMake自体の自動再構成は、CppBuildなしと同じく有効のまま。指定すれば選んだProjectだけを上と同じ方法で削除する。全体cleanはSolutionのビルド設定（構成・生成環境）を使う。
- **共有依存の保護は廃止**（2026-10-01）：他Projectが使うライブラリを削除しても、次のビルドでCMakeが自動で作り直す。以前の「対象外Projectの依存を保護する」処理と、そのための依存解決は行わない。
- ビルドディレクトリが未作成または空、空の対象選択は成功（commandが空のProcessReport）。所有マーカー不一致・未所有・キャッシュや生成ファイル（VSはソリューションファイル、Ninjaはbuild.ninja）の欠落は失敗結果とし、自動修復・生成は行わない。
- ソース・保存設定・CMakeキャッシュ・生成ファイルは保持する。MSVCの.pdb・.ilk・.exp等、cleanが対象にしない中間ファイルは残る場合がある。

rebuildは、構成してから対象をcleanし、ビルドする。Solution.rebuildは`build_projects`未指定ならツリー全体をcleanする。

# Projectの形式切り替え（2026-09-30更新）

ライブラリProjectは`STATIC_LIBRARY`・`SHARED_LIBRARY`・`INTERFACE_LIBRARY`の3形式を最初から選択できる。`EXECUTABLE`と`TEST`はそれぞれ固定で、他形式への変更や混在を拒否する。

```python
from cppbuild import ProjectBuildSettings, ProjectType

library = solution.add_project("Library", "Library", ProjectType.STATIC_LIBRARY)
library.set_build_settings(ProjectBuildSettings(project_type=ProjectType.INTERFACE_LIBRARY))
report = library.build()
```

INTERFACE_LIBRARYは自身のライブラリバイナリを生成せず、インクルードパス・公開定義・依存関係を利用側へ渡す。宣言・型・マクロだけのヘッダーも扱える。.cppがあってもそのProjectのソースはコンパイルせず、VSのファイル一覧には表示する。CMakeによるコンパイラー検出の試験コンパイルは通常どおり発生し得る。

選択を省略すると、保存された作成時形式ProjectSettingsData.initial_typeを使う。initial_typeは作成時に設定され、通常saveでは変更できない管理情報。実行時のProjectBuildSettings.project_typeは保存しないため、再open後は作成時形式へ戻る。set_build_settingsはビルド設定全体を置き換えるので、構成・実行引数・ツール指定等を引き続き使う場合は、それらも渡す。

ライブラリのtypesには3形式それぞれのTypeSettingsDataを保持する。作成・旧設定移行時に不足形式を既定設定で補い、既存形式の設定は保持する。別形式のinclude_directoriesや定義を自動コピーしないため、必要に応じて種類別設定を変更する。saveで形式の一部を削除したり、実行ファイルやTESTへ変換したりすることはできない。

依存の形式は、link_project/link_solutionの第2引数で保存される。提供側のビルド設定で `project_type` に静的または共有を明示すると、そのビルドの静的・共有リンクはすべてその形式になる。外部Projectは最上位の `SolutionBuildSettings.project_types` で指定する（`ProjectBuildSettings.project_type` より優先。`INTERFACE_LIBRARY` の明示との併用はエラー）。保存済みの指定と管理ファイルは変わらない。インターフェースとして保存されたリンクは切り替わらない。明示しなければ、同じProjectを別の利用側が静的・共有・インターフェースとして同時に使える。ソースの自動変換は行わないため、静的・共有を使う場合はその形式でビルドできる実装が必要になる。

2026-10-01更新（CMakeでの表現）：形式の選択は生成ファイルのキャッシュ変数 `<Solution>_<Project>_TYPE` で、CppBuildはその値を構成ごとに渡す。1つのビルドツリーの中で、必要な形式ごとにターゲットを作る（作成時形式は `<Solution>_<Project>`、それ以外は `_static`・`_shared`・`_interface` を付ける。zlib/zlibstaticと同じ形）。静的と共有を同時に作る場合、選択された形式（既定は静的）以外の成果物名に `-shared`／`-static` を付け、静的ライブラリとDLLのインポートライブラリが同じ名前にならないようにする。形式を切り替えても両方のビルド結果がツリーに残るため、元に戻したときは再コンパイルしない。

旧APIのHEADER_ONLYはINTERFACE_LIBRARYへ書き換える。2026-10-17更新：旧schema（header_onlyを含む）の移行はなくなった。

移行には設定の書き込み権限が必要。旧種類設定ファイルと旧vs2022-<architecture>-header_only生成物は自動削除しない。新名称の生成物はupdate/buildで生成する。テンプレート・Project移動でも作成時形式を保持する。

# 生成器・コンパイラの切り替え（2026-09-30追加）

```python
from cppbuild import CMakeSettings, Environment, EnvironmentOptions, ProjectBuildSettings, SolutionBuildSettings

# 省略時はOSごとの固定値：Windows=Visual Studio 17 2022、その他=Ninja Multi-Config
solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(generator="Ninja Multi-Config")))
solution.build()

# Visual Studio 2026（.slnx）＋v143ツールセット、32ビット
solution.set_build_settings(SolutionBuildSettings(
    architecture="Win32", cmake=CMakeSettings(generator="Visual Studio 18 2026", toolset="v143")))

# Ninja＋特定のコンパイラ（名前または絶対パス）／ツールチェーンファイル（絶対パス）
CMakeSettings(generator="Ninja Multi-Config", cxx_compiler="clang++", c_compiler="clang")
CMakeSettings(generator="Ninja Multi-Config", toolchain_file="/opt/toolchains/arm.cmake")

# Projectだけ別の環境にする（CMakeSettingsはオブジェクト単位で親から継承）
project.set_build_settings(ProjectBuildSettings(cmake=CMakeSettings(generator="Ninja Multi-Config")))

for info in Environment.generators():          # CMakeの生成器一覧＋本ライブラリの対応可否
    print(info.name, info.supported)
report = Environment.check(EnvironmentOptions(cmake=CMakeSettings(generator="Ninja Multi-Config")))
```

- 対応生成器は `Visual Studio 17 2022`・`Visual Studio 18 2026`・`Ninja Multi-Config`。それ以外はCMakeが対応していても `SettingsError`。`Environment.generators()` の `supported` は本ライブラリの実装の有無で、各OS・コンパイラでの検証済みを意味しない。検証済みの組み合わせは実装記録を参照。
- コンパイラの検出はCMakeが行う。VSの生成器はVSのMSVCを使い、`toolset`（例 `v143`）で切り替える。VSでは `c_compiler`/`cxx_compiler` を指定できない。
- WindowsでNinjaを使い、コンパイラ無指定または `cl` を指定した場合は、ライブラリがvswhereでVSを探し、vcvarsallでMSVCの環境を用意する。開発者コマンドプロンプトは不要。`toolset` にMSVCのバージョン（例 `"14.44"`）を指定すると、そのバージョンを使う。`ToolSettings.environment` に開発者コマンドプロンプトの環境（`VSCMD_ARG_TGT_ARCH`）を渡した場合はそれを使う。ninjaはPATH、なければVS付属のものを使う。
- Linux/macOSでは、コンパイラ無指定ならCMakeの通常の探索（環境変数 `CXX`、PATH上の `c++` 等）に従う。ninjaはPATHに必要。
- `architecture` は既定 `None`（ホスト／コンパイラの既定）。`x64`・`Win32`（32ビットx86）・`ARM64` はどの生成器でも指定できる。VSでは `-A`、Ninja＋MSVCではvcvarsallの対象、macOSでは `CMAKE_OSX_ARCHITECTURES` に対応させる。GCC/Clangでは構成後にCMakeが検出した対象と照合し、違えば失敗にする（別アーキテクチャ向けはツールチェーンファイルで指定）。
- `UpdateReport.generator` と `UpdateReport.compiler`（`CompilerInfo`：id・version・path・architecture）で、実際に使われた生成器とコンパイラを確認できる。VS以外ではsolution_file/project_file/filters_fileはNone。2026-10-01更新：solution_fileはSolutionの.sln／.slnx（Projectごとの.slnはない）、project_fileはSolutionツリー内のそのProjectの.vcxproj。
- 2026-10-01更新：ビルドツリーはSolutionごと・生成環境（生成器・アーキテクチャ・ツールセット・コンパイラ・ツールチェーン）ごとに1つ。全体と個別の操作で同じツリーと成果物を共用する。生成器を切り替えても、前の環境のツリーは削除しない。
- 依存でつながるProject、全体操作の全Project、外部Solutionの上書き設定では、構成と生成環境が一致している必要がある。2026-10-01更新：外部Solutionは、`external_build_settings` を指定しなければ最上位Solutionの構成・アーキテクチャ・C++標準・ToolSettings・CMakeSettingsを引き継ぐ（同じツリーで構成するため）。
- 2026-10-01更新：全体ビルドはどの生成器でも、ツリーで選択Projectのターゲット（未指定なら既定のall）を `cmake --build` する。依存順はCMakeが決める。VSでは生成した.sln／.slnxをそのままIDEで開いてビルドできる。
- 実行できない対象（x64ホストでのARM64ビルド等）は、ビルドはできるが、run/testは実行前にエラーまたは失敗結果になる。
- 共有ライブラリの実行時探索：実行ファイルとDLLは既定で同じ `bin/<構成>` に出る。成果物の出力先を変えた場合に備え、run/testではWindowsはPATHに依存先の.dllの場所を追加する。Linux/macOSはCMakeがビルドツリーに設定するRPATHを使い、補助として `LD_LIBRARY_PATH`／`DYLD_LIBRARY_PATH` も設定する。
- `ImportedLibrary` の共有ライブラリのインポートライブラリは、インポートライブラリを使う対象（MSVC・MinGW）でだけ必須。
- WindowsのNinja＋MSVCでは、パスに非ASCII文字（日本語等）があると、ヘッダー依存を追跡できない。MSVCの `/showIncludes` が出力するパスはコンソールのコードページで、Ninja 1.13はUTF-8として比較するため。この場合、ビルドは正しく完了するが、毎回再コンパイル・再リンクされる（差分ビルドにならない）。ASCIIパスでは差分ビルドになることを確認済み。VSの生成器にはこの制約はない。
- ツールチェーンファイルの内容の変更や、コンパイラ無指定時の環境変数以外の変化は検出しない。この場合はSolutionのintermediate_directoryを別の場所へ変更して再構成するか、UpdateReport.build_directoryで示される該当ツリーを利用者が削除する。
- ファイル・Projectの移動とテンプレート展開は、Linux/macOSでも既存の宛先を上書きしない。ディレクトリは宛先を排他作成してからrename、ファイルはハードリンクしてから元を削除する（ハードリンク非対応なら排他作成してコピー）。

# 依存探索ディレクトリとリンク形式の切り替え（2026-10-01追加）

```python
from cppbuild import MissingDependenciesError, ProjectType, SolutionBuildSettings

data = ecs.settings.get()
data.dependency_directories = ["deps"]      # ECS/deps/A, ECS/deps/B, ECS/deps/STL ...
ecs.settings.save(data)

try:
    ecs.build()
except MissingDependenciesError as error:
    for missing in error.missing:           # clone すべき GUID の一覧
        print(missing.project_guid, missing.required_by, missing.registered_locations)

# この操作だけ STL を共有ライブラリとしてリンク（project.json は変更しない）
ecs.set_build_settings(SolutionBuildSettings(project_types={stl_guid: ProjectType.SHARED_LIBRARY}))
```

- 菱形の依存（ECS→A→STL、ECS→B→STL）では、A・Bが自分の `deps/STL` を登録していても、最上位の `ECS/deps/STL` が使われる。ECSにSTLへの依存を追加する必要はない。clone内に `deps/` がなくてもよい。
- 最上位Solutionが自分で `link_solution` した所在は、探索ディレクトリより優先する（作業版を指す場合など）。
- 2026-10-17更新：探索ディレクトリの下を深さに関係なく探し、`.cppbuild/project.json`（Solution）を見つける（リポジトリの中の `build/cppbuild/.cppbuild` のような置き場所でもよい）。1つのリポジトリに複数のSolutionがあってもよい。見つけたSolution自身の依存探索ディレクトリの中（そのSolutionが同梱する依存のコピー）は、同じ子ディレクトリの中にある場合に除く。これにより、菱形の依存で `deps/A/deps/STL` と `deps/STL` が「同じGUIDが2か所」にならない。入らないディレクトリは、`.cppbuild`・`.git`・`__pycache__`・`.cache`・`.venv`・`node_modules` という名前のもの、シンボリックリンク・ジャンクション、CppBuildの出力先、clone中の一時ディレクトリ、CMakeのビルドツリー（`CMakeCache.txt` があるもの）。`build` という名前のディレクトリには入る。ディレクトリだけをたどり、実測で18,544ディレクトリに0.84秒（ウォームキャッシュ）。同じGUIDが2か所にあるとエラーになる。従来は直下の `<子>/.cppbuild` だけを探していた（これも引き続き見つかる）。
- 依存先Projectの `ProjectBuildSettings(project_type=...)` でも、静的⇔共有を切り替えられる。外部Solution内のProjectは、最上位の `project_types`（GUID指定）で切り替える。
- 2026-10-07追加：Windowsで `__declspec(dllexport)` を付けていないライブラリを共有にすると、DLLにインポートライブラリ（.lib）ができず、利用側のリンクが `LNK1104` で失敗する。ライブラリProjectで `project.settings.set_windows_export_all_symbols()` を呼ぶと、生成する共有ライブラリのターゲットにCMakeの `WINDOWS_EXPORT_ALL_SYMBOLS ON` を付け、ソースを変えずに静的⇔共有を切り替えられる。Project単位の保存する管理設定（`ProjectSettingsData.windows_export_all_symbols`、既定False、`False` を渡すと解除）で、生成ファイルに書くためCppBuildなしのビルドにも効く。静的・INTERFACEの形式とWindows以外には影響しない。Solution単位の設定は設けない（CppBuildなしでは、CMake標準の `CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS=ON` を構成時に渡せば全ターゲットに効く）。データメンバー（変数）は書き出されないため、DLL越しに使う変数には従来どおり `__declspec(dllimport)` が必要。

# C++プログラムの基本設定（2026-10-12追加）

```python
# 入口の関数。main（既定）・wmain・WinMain・wWinMain はツールチェーンが通常どおり見つける。
# WinMain・wWinMain はGUIアプリ（/SUBSYSTEM:WINDOWS）になる。
app.settings.set_entry_point("wWinMain")
# それ以外の名前は /ENTRY でリンクする（MSVC専用）。gui でサブシステムを選ぶ（既定はコンソール）。
app.settings.set_entry_point("AppMain", gui=True)
app.settings.set_entry_point()  # main に戻す

# OSやツールチェーンのライブラリを名前でリンクする（ライブラリなら利用側にも伝わる）
net.settings.set_system_libraries(["ws2_32"])
```

- **MSVCの既定**：生成するすべてのC++ターゲットに、MSVCのとき `/utf-8`（ソースと実行時の文字コードをUTF-8にする）と `/Zc:__cplusplus`（`__cplusplus` を選んだ標準の値にする）を付ける。ライブラリでは利用側にも伝わる（ヘッダーも同じ読み方が必要なため）。従来は、日本語を含むUTF-8（BOMなし）のソースがコードページ932として読まれ、文字列があるとビルドが失敗していた（`C4819`・`C2001`）。clang-clはもともとUTF-8で読むため対象外。
- **入口の関数**（実行ファイルのProjectだけ、保存する管理設定 `entry_point`・`gui`、既定のときは `project.json` に書かない）：`WinMain`・`wWinMain` を選ぶとCMakeの `WIN32_EXECUTABLE` を付ける。標準以外の名前は `/ENTRY:<名前>` と `/SUBSYSTEM:CONSOLE`／`WINDOWS` でリンクする。CRTの開始処理を通らないため、グローバル変数のコンストラクター・`atexit`・コマンドライン引数の準備は行われない。DLL版のCRTは読み込み時に自分で初期化されるため、そのインポートライブラリ（Debugは `ucrtd`・`vcruntimed`、それ以外は `ucrt`・`vcruntime`）をリンクし、CRTの関数は使える。関数は `extern "C"` で定義する。MSVC以外では構成時にエラー。標準の名前と `gui` の食い違い（`main` でGUIなど）や、実行ファイル以外への指定はエラー。
- **OSのライブラリ**（保存する管理設定 `system_libraries`）：`ws2_32`・`d3d11`・`pthread` のような名前だけを受け付ける（パス・`-l` 付き・空白は不可）。`target_link_libraries` に名前のまま渡す。従来は `ImportedLibrary` でファイルのパスを指定するしかなかった。
- **リソースとマニフェスト**：Windowsでは、ソースディレクトリの `.rc`（アイコン・バージョン情報・文字列）と `.manifest` もビルドする（従来は表示だけ）。
- **gMock**：TESTのProjectで `BUILD_GMOCK` を既定ONにし、`GTest::gmock` もリンクする。`#include <gmock/gmock.h>` と `MOCK_METHOD` が使える。

# Visual Studioのプロパティに相当する設定（2026-10-13追加）

```python
from cppbuild import BuildOption, CMakeSettings, SolutionBuildSettings

# 任意のコンパイル／リンクオプション（1要素が1引数）。compiler（CMakeのコンパイラID）と configuration で条件を付けられる
app.settings.set_compile_options(["/MP", BuildOption("/arch:AVX2", compiler="MSVC", configuration="Release")])
app.settings.set_link_options([BuildOption("/STACK:4194304", compiler="MSVC")])
# 構成やコンパイラで条件を付ける定義（NAME または NAME=VALUE）
app.settings.set_conditional_definitions([BuildOption("APP_DEBUG", configuration="Debug"),
                                          BuildOption("_CRT_SECURE_NO_WARNINGS", compiler="MSVC")])
# 警告レベル 0〜4 と、警告をエラーにするか
app.settings.set_warnings(4, as_errors=True)
# ビルドイベント（引数のリスト、Projectのディレクトリで実行）
app.settings.set_build_events(pre_build=[["python", "tools/gen.py"]], post_build=[["cmake", "-E", "echo", "done"]])
# 毎回のビルドで実行ファイル（またはDLL）の横へコピーするファイル・ディレクトリ
app.settings.set_output_files(["data/config.json", "assets"])
# Solution全体のMSVCランタイム（"dll"＝/MD が既定、"static"＝/MT）
solution.settings.set_msvc_runtime("static")
# Windows SDKのバージョン（保存しない生成環境の設定）
solution.set_build_settings(SolutionBuildSettings(cmake=CMakeSettings(windows_sdk="10.0.22621.0")))
```

- **共通**：Solutionのランタイム以外は、Project単位の保存する管理設定（`ProjectSettingsData`）。既定値のときは `project.json` に書かない（schema_versionは変えない。書いたファイルは旧版のCppBuildでは読めない）。生成ファイルに書くので、CppBuildなしのビルドにも効く。どれもそのProject自身のビルドだけに効き、利用側には伝わらない。INTERFACE_LIBRARYの形式では使われない。
- **オプション・定義**：値は1つの引数。`;`・改行・`$<`・引用符・バックスラッシュ・`$` は使えない（CMakeの引数や生成式を壊すため）。`>`・`,` は使える。`compiler` は `MSVC`・`GNU`・`Clang` などのCMakeのコンパイラID、`configuration` は `Debug`・`Release`・`RelWithDebInfo`・`MinSizeRel`。オプションはCとC++のソースにだけ渡す（`.rc` には渡さない）。リンクオプションは静的ライブラリには使われない。`/MP`・`/sdl`・`/ZI`・`/fp:fast`・`/GL` と `/LTCG`・`/fsanitize=address`・`/DELAYLOAD:x.dll`（`delayimp` を `set_system_libraries` で）・`/NODEFAULTLIB:x` などはこれで指定する。Unicode版のWin32 APIは定義 `UNICODE`・`_UNICODE`。
- **警告**：MSVCは `/W0`〜`/W4`、GCC・Clangは 0=`-w`、1・2=`-Wall`、3=`-Wall -Wextra`、4=`-Wall -Wextra -Wpedantic`。エラー化はCMake標準の `COMPILE_WARNING_AS_ERROR`（`cmake --build` の `--compile-no-warning-as-error` で一時的に外せる）。**指定しないときは、MSVCではVisual Studioと同じ `/W3`**（2026-10-13修正：従来はCMakeが `/W3` を付けなくなったため `/W1` で、VSなら出る警告が出ていなかった）。
- **ビルドイベント**：ビルド前（VS以外の生成器ではリンク前と同じ時点）・リンク前・ビルド後。引数は文字どおり渡す（`${...}` は展開しない）。コマンドはPATHから探す。
- **出力へのコピー**：パスはProject内（Project自身・管理ディレクトリは不可）。Project内での相対位置を保って、ビルドした実行ファイル・DLL（実行ファイル・TEST・共有ライブラリの形式）の横へ、毎回のビルドでコピーする（ファイルは変わったときだけ、ディレクトリはCMake 3.26以降で変わったものだけ）。ソースを変えずにデータだけ変えても反映される。コピー用のターゲット `<ターゲット名>_files` を作る（名前の衝突を検査する）。
- **MSVCランタイム**：Solutionの保存する管理設定 `msvc_runtime`。最上位として構成したときに `CMAKE_MSVC_RUNTIME_LIBRARY` を設定し、リンク先Solution・GoogleTestを含むビルド全体に効く（リンク先Solution自身の設定は使わない。食い違うとリンクできないため）。CppBuildなしでは `-DCMAKE_MSVC_RUNTIME_LIBRARY=...` で上書きできる。`/ENTRY` の入口では、ランタイムに合わせて `libucrt`・`libvcruntime` 系をリンクする（静的ランタイムはCRTの開始処理なしでは初期化されないため、CRTの関数を使えない場合がある）。
- **Windows SDK**：保存しない `CMakeSettings.windows_sdk`。VSの生成器は `CMAKE_SYSTEM_VERSION`、Ninja＋MSVCは `vcvarsall` の引数で選ぶ。指定したときはビルドツリーが別になる（指定しないときの既存のツリーはそのまま）。MSVC以外ではエラー。
- **Cのソース**（2026-10-13追加）：`.c` もコンパイルする（全Solutionで `project(... LANGUAGES C CXX)`）。C++のPCHはCのソースに使わない。MSVCでは `/utf-8` も付ける。従来 `.c` は表示だけだったため、ビルドしたくない `.c` をソースディレクトリに置いている場合はビルドの対象になる。
- **`.def`**（Windows）：モジュール定義ファイルをリンクに渡す。`__declspec(dllexport)` なしで関数を書き出せる。
- **C++20モジュール**：`.ixx`・`.cppm`・`.mpp`・`.ccm`・`.cxxm` をモジュールのインターフェースとしてビルドする（CMakeの `CXX_MODULES` ファイルセット、CMake 3.28以降が必要）。ライブラリのモジュールは利用側から `import` できる。モジュールの依存を調べるスキャンは、モジュールを持つターゲットと、それを（間接的にも）リンクするターゲットだけに付ける（全ソースのスキャンはビルドが遅くなるため）。
- **外部のDLL**（2026-10-13修正）：Windowsでは、実行ファイル・TESTのビルド後に、読み込むDLL（CMakeの `$<TARGET_RUNTIME_DLLS>`）を実行ファイルの横へコピーする。従来は `ImportedLibrary` の共有ライブラリのDLLがどこにもなく、`run`・F5・テストで起動できなかった（`0xC0000135`）。
- **意図して設定にしないもの**：出力ファイル名（Project名で固定し、名前の衝突を検査するため）、F5の実行引数・環境変数（実行引数は保存しないビルド設定のため）。

# gitのURLによるリンクと自動取得（2026-10-03追加）

リポジトリをcloneしただけの人が、CppBuild・Pythonなしで `cmake -S . -B build` だけでリンク先も含めてビルドできるようにする。リンク先は、CppBuildのSolution（生成ファイルをコミット済み）を含むgitリポジトリとする。

```python
from cppbuild import GitFetchError, GitSourceConflictError, ProjectType, SolutionBuildSettings

data = app_solution.settings.get()
data.dependency_directories = ["deps"]          # clone は先頭の依存探索ディレクトリの直下に置く
app_solution.settings.save(data)

# 第1引数：URL（必須）、第2引数：ブランチ・タグ・コミット（省略時は既定ブランチの最新）、
# 第3引数：リポジトリのルートからSolutionの .cppbuild への相対パス（省略時は ".cppbuild"）
app.settings.link_git("https://example.com/team/STL.git", link_type=ProjectType.STATIC_LIBRARY)
app.settings.link_git("https://example.com/team/Mono.git", "v1.2", "libs/Core/.cppbuild",
                      link_type=ProjectType.STATIC_LIBRARY)

app_solution.settings.get().git_sources        # この Solution の記録：名前 -> GitSource(url, revision, path)
app_solution.git_sources()                     # 間接依存を含む一覧（GitSourceStatus：記録元・clone の有無）
app_solution.set_git_source("https://example.com/team/STL.git")           # 記録を既定ブランチの最新へ
app_solution.set_git_source("https://example.com/team/STL.git", "v2.0")   # タグ・ブランチ・コミットへ
report = app_solution.fetch_git_sources()       # 足りない clone を取得（report.fetched / report.warnings）
app_solution.remove_git_source("STL")           # どのProjectもリンクしなくなった記録を削除（clone は残す）
```

- **記録**：リンクした時点で決まったコミットID（40桁）を、Solutionの `project.json` の `git_sources` に保存する。ブランチ名やタグ名のままでは記録しない。置き場所の名前はURLのリポジトリ名（同名の別URLには短い識別子を付ける）で、どのPCでも同じになる。認証情報を含むURL（`https://user:token@...`）は拒否する。認証は利用者のgitの設定に任せる。
- **リンク**：主Project（`main_project`）にリンクする（`link_solution` と同じ）。依存・参照・記録を一緒に保存する。リンク先が置き場所になければcloneし、あればそのまま使う。
- **間接依存**：リンク先Solutionが自分で記録したgitのリンク先も、最上位の置き場所（`deps/` 直下）へ取得する。同じリポジトリの記録が食い違う場合は、最上位の記録を使う。最上位に記録がなく食い違う場合は `GitSourceConflictError`。`set_git_source` で最上位に記録すると解消する。
- **既存の置き場所**：すでにSolutionがあれば、削除・更新・切り替えをしない（編集中の作業版を守る）。記録と手元のコミットが違えば、`fetch_git_sources().warnings` と構成時のCMakeの警告で知らせる。`set_git_source` も記録だけを変え、cloneは変えない。
- **自動取得**：CppBuildの操作（update・build・run・test等）は、足りないcloneを先に取得する。`SolutionBuildSettings(fetch_git=False)` で無効にできる。gitのパスは `ToolSettings(git=...)`。
- **CppBuildなしの構成**：最上位の `CppBuildTopLevel.cmake` に、間接依存を含む全リンク先のURL・コミット・置き場所が並ぶ。構成時に、置き場所がなければ一時ディレクトリへcloneして記録のコミットをcheckoutし、置き場所へ移す。`-DCPPBUILD_FETCH=OFF` で取得を無効にできる。
- **取得できない場合**：gitがない、オフライン、権限がない、コミットがない場合は、対象の一覧（置き場所・URL・コミット・理由）と、手で置くコマンド（`git clone <URL> <置き場所> && git -C <置き場所> checkout <コミット>`）をエラーで示す（Pythonは `GitFetchError.failures`）。
- **生成形式の確認**：リンク先の生成ファイルが別の版のCppBuildで作られ、取り決めが合わない場合は、構成時にエラーにする（各Solutionの `CMakeLists.txt` が `CPPBUILD_FORMAT` を宣言し、取り込む側が確認する）。
- **リンク先リポジトリの前提**：CppBuildのSolutionを含み、その生成ファイル（`.cppbuild`の中の `CMakeLists.txt`・`CppBuildTopLevel.cmake`・`projects/*/CMakeLists.txt`）をコミットしていること。`deps/` と `.cppbuild/output/`・`.cppbuild/operations/` はコミットしない。2026-10-17追加：`.cppbuild`はリポジトリのどこにあってもよい（第3引数で指定）。リンク先SolutionのProjectがリポジトリの外にあると、cloneだけではビルドできないため、`link_git` はエラーにして何も残さない。
- **制約**：パス指定（`link_solution`）でリンクしたSolutionの中のgitの記録はたどらない（gitでリンクしたSolutionの記録だけをたどる）。1つのリポジトリから使えるSolutionは1つ。サブモジュール・Git LFSは扱わない。`remove_git_source` は記録だけを消し、cloneは削除しない。


# CppBuildを組み込むプログラム向けの機能（2026-10-13追加）

CppBuildを組み込んで使うプログラムが、ファイルの置き方・例外の文言・値の検査をCppBuildから写さずに済むようにする。

```python
import cppbuild
from cppbuild import (AlreadyRegisteredError, FileKind, InvalidValueError, NoMainProjectError,
                      NotRegisteredError, ProjectType, SolutionBuildSettings)

# 設定の変更とupdate()をひとまとまりにする。ブロックを抜けるとupdate()を実行し、
# ブロックの例外かupdate()の失敗で、ブロック内で書いた管理ファイル・生成ファイルを元に戻す
with solution.transaction() as change:
    app.settings.link_package(package)
    app.settings.set_compile_options(["/W4"])
if not change.success:
    print(change.restored, change.restored_paths)       # 戻したか、戻したファイル
    print(change.update.processes[0].output)            # 失敗したCMakeの出力

# 何も変わっていなければ、update()・build()等はCMakeを実行しない（0.02〜0.03秒程度）
report = solution.update()
report.processes[0].skipped                              # Trueなら構成を省いた

# 例外を種類で見分ける（どれも SettingsError の子。NotRegisteredError は KeyError でもある）
try:
    app.settings.link_solution(path, ProjectType.STATIC_LIBRARY)
except AlreadyRegisteredError as e:                      # e.kind == "dependency"、e.name は既存のリンクの dependency_id
    ...
except NoMainProjectError as e:                          # e.solution、e.project_type（None なら主Projectがない）
    ...
except InvalidValueError as e:                           # e.field、e.value、e.allowed（決まった値の一覧か None）
    ...

# 受け付ける値
cppbuild.CPP_STANDARDS, cppbuild.CONFIGURATIONS, cppbuild.ARCHITECTURES, cppbuild.GENERATORS
cppbuild.MSVC_RUNTIMES, cppbuild.LIBRARY_TYPES, cppbuild.SWITCHABLE_TYPES

# ファイルの種類（基準ディレクトリからの相対パスか絶対パス）
solution.file_kind(".cppbuild/projects/App/CMakeLists.txt")   # FileKind.GENERATED
solution.file_kind(".cppbuild/project.json")  # FileKind.MANAGED
solution.file_kind("App/src/main.cpp")     # None（CppBuildのファイルではない）

# 静的・共有を切り替えられるライブラリを、リンク先も含めてすべて共有ライブラリにする（保存しない）
solution.set_build_settings(SolutionBuildSettings(shared_libraries=True))

# gitのリンク先を、全Projectのリンクと記録ごと外す（cloneは残す）
report = solution.unlink_git_source("STL")  # report.unlinked: ((Project名, dependency_id), ...)
```

- **トランザクション**：`with solution.transaction() as change:` の中で設定を変える。ブロックを抜けると `solution.update()` を実行し、結果を `change.update` に入れる。ブロックが例外を出したとき（例外はそのまま伝わる）、`update()` が例外を出したとき、`update()` が失敗したときは、ブロック内でCppBuildが書いた・消した管理ファイル（`.cppbuild/` の設定・種類別設定・テンプレート。`remove_project` で消した設定を含む）と生成ファイル（`.cppbuild` の中の `CMakeLists.txt`・`CppBuildTopLevel.cmake`）を書く前の内容に戻し、新しく作ったファイル・空のディレクトリを消し、設定を読み直す。`link_git` が新しくしたcloneも消す。`change.success` は更新が成功して戻していないとき真。
  - 戻さないもの：ソースファイル（`add_file` 等）、ビルドツリー・成果物。ビルドツリーは失敗した構成のままだが、次の操作で構成し直す。
  - 途中で別のプロセスが書き換えたファイルは戻さず、`change.conflicts` に入れる。トランザクションはロックを保持し続けないため、同時に変更すること自体は防がない。
  - `move_project` はファイルを移動するため、トランザクション内ではエラー。入れ子もエラー。
- **構成の省略**：前回の構成が成功していて、CppBuildが渡すもの（生成ファイル・初期キャッシュ・構成のコマンド・集めたソースの一覧・ツールの設定）が同じで、CMakeが前回読んだファイル（File APIの `cmakeFiles-v1`）がどれも変わっていなければ、CMakeを実行しない。結果の `ProcessReport.skipped` が真になる。ソースの追加・削除、設定の変更、生成ファイルの手直し、ビルドツリーの削除、パッケージの設定ファイルの更新では構成し直す。記録はビルドツリーの `cppbuild-configured.json`。
- **例外の種類**：`AlreadyRegisteredError`（`kind`：`project`・`dependency`・`file_template`）、`NotRegisteredError`（`kind`：`project`・`dependency`・`file_template`・`git_source`。従来 `KeyError` だった箇所の互換のため `KeyError` の子でもある）、`NoMainProjectError`、`InvalidValueError`（構成・アーキテクチャ・C++の規格・生成器・MSVCランタイム・警告レベル・名前・OSのライブラリ名・CMakeのターゲット名とパッケージ名・リンク形式）。パスがない場合は従来どおり `FileNotFoundError`。ほかの検査は従来どおり `SettingsError` で、文言で区別する。
- **ファイルの種類**：`GENERATED`（管理ファイルから `update()` が作る。CppBuildなしでビルドできるようコミットする）、`MANAGED`（`.cppbuild/` の中の設定。コミットする）、`OUTPUT`（ビルドツリー・成果物・ロック。コミットしない）、`FETCHED`（`git_sources` のclone。記録から取得し直せる。コミットしない）。CppBuildの印のない `CMakeLists.txt` は `None`。手で置いたリンク先Solutionの中のファイルも同じ規則で分類する（そのリポジトリのファイルとして扱うかは利用側で決める）。
- **すべて共有**：`SolutionBuildSettings.shared_libraries=True` は、最上位の操作で、静的・共有のどちらかになるライブラリ（リンク先Solutionを含む）を共有ライブラリにする。`project_types` と、そのProject自身の `ProjectBuildSettings.project_type` の指定が優先する。INTERFACE_LIBRARYのままのものは変えない。dllexportのないライブラリは `set_windows_export_all_symbols()` が必要。
- **リンク先単位の解除**：`unlink_git_source(name)` は、このSolutionの全Projectから、そのcloneの中のProjectへのリンクを外し、`git_sources` の記録を消す。1回の保存で行う。cloneは残す（`remove_git_source` と同じ）。
- **Solutionの外のファイルからのテンプレート**：`create_file_template(name, source_file)` の `source_file` には絶対パスを渡せる（従来から可能）。素材はSolutionの `.cppbuild/templates/` に写す。2026-10-17更新：`file_templates` の値は `.cppbuild/templates/` からの名前（例 `Header.hpp`）。`set_file_template` に渡すファイルも `.cppbuild/templates/` の下になければならない。
