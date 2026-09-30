# 利用手順

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

移動先はSolutionルート基準で、まだ存在しないフォルダーを指定する。実フォルダーと登録先を一緒に移し、内部依存・既存Projectオブジェクト・非保存設定を保持する。既定では全体.slnと依存利用側も再生成するが、コンパイルはしない。必要に応じてbuild/run/testを実行する。

旧キャッシュは全体`.cppbuild/relocations/<id>/`へ退避する。退避パスはreport.changed_pathsにも含む。管理設定とソースは移動先に保持される。再生成失敗時も移動自体は完了しているため、原因を直してsolution.updateを再実行する。移動前から実フォルダーが存在しない場合の登録修復には対応しない。

他のビルド・設定操作を停止し、非同期runはwaitしてから呼び出す。他Solutionの利用側は別途updateする。利用スクリプトやC++本文、任意の引数・環境変数に埋め込んだパスは必要に応じて修正する。リンクやジャンクションを含む移動は拒否する。

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
)
solution.settings.save(settings)
report = solution.update()
```

`project_folders` のキーは自分の所属Project名。値は `Projects` 配下の `/` 区切り相対階層で、未指定・空文字列なら直下。絶対パス・`..`・外部Project名は拒否する。ルートの二つのフォルダー名は単一階層で、同名にできない。

リンク先は `LinkedProjects/<相手のSolution名>` に全所属Projectを表示する。間接リンク先も同じ階層へ集約し、同じSolutionは重複表示しない。同名の別Solutionはパス由来の短い識別子を表示名へ付ける。表示用に追加した未参照Projectは通常のビルド対象に加えないが、表示する `.vcxproj` の生成には、そのProjectの有効な設定・ソース・外部依存が必要になる。TESTの構成ではGoogleTestの取得が必要になり得る。

未参照Projectは明示選択、または作成時形式で表示する。選択可能なライブラリ3形式をすべて表示するための生成は行わない。実際の依存で参照済みのProjectは要求された種類を表示する。全体操作の構成・architecture・ToolSettingsの整合条件は表示用Projectにも適用する。外部Solutionの設定は `external_build_settings` で指定できる。

保存・再open・テンプレート復元で表示設定を保持する。Project登録解除時は対応する配置設定も削除する。`settings.solution_folders = None` を保存して全体updateすると、従来の依存Projectのみの階層なし表示へ戻る。個別Projectのupdateでは全体表示を変更しない。

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

旧管理ファイルはopen/reload時にschema_version=4へ自動保存して移行する。旧別名を同じ対象GUIDへ統合し、解除用IDはすべて維持する。初回は書き込み権限が必要で、対象GUIDが未保存の旧外部リンクはリンク先も必要。利用側と外部Solutionの相対配置を変えた場合は、全利用元でunlinkして新しい所在へ再リンクする。相対配置を維持した一括移設では設定変更は不要。GUIDだけで移動先を探索する機能はない。

# 別の環境への移設と相対パス（2026-09-30追加）

管理JSONのパスは、そのJSONがあるディレクトリからの相対パスで保存する。APIへ絶対パスを渡しても保存時に変換する。APIに相対パスを渡す場合は、従来どおりSolution設定がSolutionルート、Project設定・リンク先がProjectルート基準。open/createの引数は作業ディレクトリ基準のまま。

例えば次の配置では、Consumerの参照一覧に `../../Provider/.cppbuild` を保存する。

```text
Workspace/
  Consumer/.cppbuild/project.json
  Provider/.cppbuild/project.json
```

Workspace全体を別の場所へ移してから `Solution.open(new_workspace / "Consumer/.cppbuild")` すれば、移設先のProviderを参照する。ソース、include、PCH、共有素材、CMakeソース／パッケージ、ImportedLibraryのDLL・LIB・includeも同じ方式で保存する。利用側と依存先の相対配置を維持すること。

- 旧形式は移設前の環境で各Solutionをopen/reloadし、schema_version=4へ自動移行してから移す。初回は設定の書き込み権限が必要。
- `.cppbuild/build`・`.cppbuild/generated` の既存CMakeキャッシュや生成物は移設先へ持ち込まず、移設先でupdate/buildして再生成する。テンプレート機能はこれらを除外する。
- ツールの所在など非保存ビルド設定は移設先で設定し直す。定義文字列・任意引数・外部CMakeListsやソース本文に埋め込まれた絶対パスは自動変換しない。
- 別ドライブや別共有など相対パスにできない参照はエラー。設定と依存先を共通のドライブ／共有配下へ配置する。

# クリーン（2026-09-30修正）

```python
report = project.clean()   # 現在の種類・architecture・configurationの成果物を削除
report = solution.clean()  # build_projectsで選択した所属Projectを削除対象にする
for process in report.processes:
    print(process.output)
```

2026-09-30更新（生成器の切り替え）：cleanは、どの生成器でもCMake標準の `cmake --build <所有ツリー> --config <構成> --target clean` を実行する。そのProjectのビルドツリーのうち、現在の構成の成果物を削除する。ツリー内部で構築するGoogleTestや外部CMakeソース（CMakeSource）の成果物も含む。以前のVS専用のMSBuild Clean（対象ターゲットだけ削除）は廃止した。他の管理Projectは各自のツリーにあり、IMPORTEDとして参照するだけなので削除されない。事前のCMake構成・再生成・ソース走査・コンパイルは行わず、全体.slnも生成しない。生成時の自動再生成抑止により、clean中の再構成は起きない。現在のProjectビルド設定を使い、全体cleanでもProjectごとの構成・ツール指定を使う。MSVCの.pdb・.ilk・.exp等、CMakeのcleanが対象にしない中間ファイルは残る場合がある。

- 個別cleanは対象の管理設定を再読込するが、外部Solutionの依存解決は行わない。ソースがなくなった場合や外部Solutionがオフラインの場合も既存ツリーをcleanできる。管理JSON自体が不正な場合は従来どおり例外になる。
- 全体cleanは所属設定を再読込し、対象外Projectの依存を解決して共有成果物を保護する。依存解決に失敗して保護範囲を確認できない場合は削除を行わず、理由を含む失敗結果を返す。表示のみの外部Projectは生成しない。
- ビルドディレクトリが未作成または空なら成功し、出力にNothing to cleanを返す。空の対象選択も成功。保護対象は削除せず理由を返す。これらはcommandが空のProcessReportで表す。
- 所有マーカー不一致・未所有・既存キャッシュや生成ファイル（VSはソリューションファイル、Ninjaはbuild.ninja）の欠落は失敗結果とし、自動修復・生成は行わない。既存ツリーを使ったClean自体の失敗もOperationReport.success=Falseになる。
- ソース・保存設定・CMakeキャッシュ・生成したVSプロジェクトは保持する。現在の構成以外の成果物、依存先と対象外Projectの成果物も保持する。キャッシュを丸ごと削除する機能は今回追加していない。

rebuildは従来どおり、ビルドに必要な生成を行ってから対象をCleanし、ビルドする。

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

依存の形式は、link_project/link_solutionの第2引数で保存される。2026-10-01更新：提供側のビルド設定で `project_type` に静的または共有を明示すると、そのビルドの静的・共有リンクはすべてその形式になる。外部Projectは最上位の `SolutionBuildSettings.project_types` で指定する。保存済みの指定と管理ファイルは変わらない。インターフェースとして保存されたリンクは切り替わらない。明示しなければ、同じProjectを別の利用側が静的・共有・インターフェースとして同時に使える。種類ごとに生成・ビルドツリーを分け、切り替え前の成果物は自動削除しない。ソースの自動変換も行わないため、静的・共有を使う場合はその形式でビルドできる実装が必要になる。

旧APIのHEADER_ONLYはINTERFACE_LIBRARYへ書き換える。旧schema_version=1/2/3のheader_only設定・内部／外部依存・ImportedLibraryはopen/reloadでschema_version=4へ移行し、GUIDと依存IDを保持する。旧設定に形式が一つならそれを作成時形式とし、複数のライブラリ形式がある場合は静的→共有→インターフェースの順で最初に存在する形式を採用する。ライブラリと実行ファイル等が混在する旧設定は、データを破棄せずエラーにする。移行前に旧版でProjectを分けるなどして整理すること。

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
- `UpdateReport.generator` と `UpdateReport.compiler`（`CompilerInfo`：id・version・path・architecture）で、実際に使われた生成器とコンパイラを確認できる。VS以外ではsolution_file/project_file/filters_fileはNone。
- ビルドツリーは生成器・アーキテクチャ・ツールセット・コンパイラ・ツールチェーンごとに分ける。既定のVS2022は従来の `vs2022-<architecture>-<種類>` を維持し、既存キャッシュを使い続ける。Ninjaは `ninja-mc-<ハッシュ>-<種類>`。同じ生成環境なら、全体と個別の操作で同じツリーと成果物を共用する。生成器を切り替えても、前の環境の成果物は削除しない。
- 依存でつながるProject、全体操作の全Project、外部Solutionの上書き設定では、構成と生成環境が一致している必要がある。
- VS2022（.sln）の全体ビルドは、従来どおり全体.slnをMSBuildでビルドする。VS2026（.slnx）は全体.slnxをIDE表示用に生成し、ビルドはNinjaと同じ方式で行う。.slnxではプロジェクトがファイル名で識別されるため、同名で種類の違うProjectを選べないことによる。
- VS以外の全体操作は、選択Projectと依存先を依存順に1回ずつビルドする。全体のIDEファイルは生成しないため、solution_folders（表示用フォルダーと未参照Projectの一覧）はVSの生成器だけに適用する。
- 実行できない対象（x64ホストでのARM64ビルド等）は、ビルドはできるが、run/testは実行前にエラーまたは失敗結果になる。
- 共有ライブラリの実行時探索：WindowsはPATHに依存先の.dllの場所を追加する。Linux/macOSはCMakeがビルドツリーに設定するRPATHを使い、補助として `LD_LIBRARY_PATH`／`DYLD_LIBRARY_PATH` も設定する。
- `ImportedLibrary` の共有ライブラリのインポートライブラリは、インポートライブラリを使う対象（MSVC・MinGW）でだけ必須。
- WindowsのNinja＋MSVCでは、パスに非ASCII文字（日本語等）があると、ヘッダー依存を追跡できない。MSVCの `/showIncludes` が出力するパスはコンソールのコードページで、Ninja 1.13はUTF-8として比較するため。この場合、ビルドは正しく完了するが、毎回再コンパイル・再リンクされる（差分ビルドにならない）。ASCIIパスでは差分ビルドになることを確認済み。VSの生成器にはこの制約はない。
- ツールチェーンファイルの内容の変更や、コンパイラ無指定時の環境変数以外の変化は検出しない。この場合は `.cppbuild/build` の該当ツリーを利用者が削除する。
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
- 探索対象は、探索ディレクトリ直下の各ディレクトリにある `.cppbuild/project.json`（Solution）だけ。再帰的には探さない。同じGUIDが2か所にあるとエラーになる。
- 依存先Projectの `ProjectBuildSettings(project_type=...)` でも、静的⇔共有を切り替えられる。外部Solution内のProjectは、最上位の `project_types`（GUID指定）で切り替える。
