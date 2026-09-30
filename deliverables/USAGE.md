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

未参照Projectが複数種類を持つ場合、種類の明示指定がなければ各種類を表示する。実際の依存で参照済みのProjectは要求された種類を表示する。全体操作の構成・architecture・ToolSettingsの整合条件は表示用Projectにも適用する。外部Solutionの設定は `external_build_settings` で指定できる。

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

旧管理ファイルはopen/reload時にschema_version=3へ自動保存して移行する。旧別名を同じ対象GUIDへ統合し、解除用IDはすべて維持する。初回は書き込み権限が必要で、対象GUIDが未保存の旧外部リンクはリンク先も必要。利用側と外部Solutionの相対配置を変えた場合は、全利用元でunlinkして新しい所在へ再リンクする。相対配置を維持した一括移設では設定変更は不要。GUIDだけで移動先を探索する機能はない。

# 別の環境への移設と相対パス（2026-09-30追加）

管理JSONのパスは、そのJSONがあるディレクトリからの相対パスで保存する。APIへ絶対パスを渡しても保存時に変換する。APIに相対パスを渡す場合は、従来どおりSolution設定がSolutionルート、Project設定・リンク先がProjectルート基準。open/createの引数は作業ディレクトリ基準のまま。

例えば次の配置では、Consumerの参照一覧に `../../Provider/.cppbuild` を保存する。

```text
Workspace/
  Consumer/.cppbuild/project.json
  Provider/.cppbuild/project.json
```

Workspace全体を別の場所へ移してから `Solution.open(new_workspace / "Consumer/.cppbuild")` すれば、移設先のProviderを参照する。ソース、include、PCH、共有素材、CMakeソース／パッケージ、ImportedLibraryのDLL・LIB・includeも同じ方式で保存する。利用側と依存先の相対配置を維持すること。

- 旧形式は移設前の環境で各Solutionをopen/reloadし、schema_version=3へ自動移行してから移す。初回は設定の書き込み権限が必要。
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

cleanは既存のビルドツリーでMSBuild Cleanを実行する。事前のCMake構成・再生成・ソース走査・コンパイルは行わず、全体.slnも生成しない。生成時の自動再生成抑止とBuildProjectReferences=falseにより依存先を巻き込まない。現在のProjectビルド設定を使い、全体cleanでもProjectごとの構成・ツール指定を使う。

- 個別cleanは対象の管理設定を再読込するが、外部Solutionの依存解決は行わない。ソースがなくなった場合や外部Solutionがオフラインの場合も既存ツリーをcleanできる。管理JSON自体が不正な場合は従来どおり例外になる。
- 全体cleanは所属設定を再読込し、対象外Projectの依存を解決して共有成果物を保護する。依存解決に失敗して保護範囲を確認できない場合は削除を行わず、理由を含む失敗結果を返す。表示のみの外部Projectは生成しない。
- ビルドディレクトリが未作成または空なら成功し、出力にNothing to cleanを返す。空の対象選択も成功。保護対象は削除せず理由を返す。これらはcommandが空のProcessReportで表す。
- 所有マーカー不一致・未所有・既存キャッシュや対象.vcxprojの欠落は失敗結果とし、自動修復・生成は行わない。既存ツリーを使ったClean自体の失敗もOperationReport.success=Falseになる。
- ソース・保存設定・CMakeキャッシュ・生成したVSプロジェクトは保持する。現在の構成以外の成果物、依存先と対象外Projectの成果物も保持する。キャッシュを丸ごと削除する機能は今回追加していない。

rebuildは従来どおり、ビルドに必要な生成を行ってから対象をCleanし、ビルドする。
