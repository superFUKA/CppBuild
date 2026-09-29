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

# 名前付きリンクの自動管理（2026-09-30追加）

```python
from cppbuild import ProjectType

# 呼び出すだけで所属Solutionに参照を登録。省略時の名前は対象ProjectのGUID。
a = app_a.settings.link_solution(external_config, ProjectType.STATIC_LIBRARY)
b = app_b.settings.link_solution(external_config, ProjectType.STATIC_LIBRARY)
references = solution.settings.get().references  # 参照は1件、利用元は2つ

# 任意名で同じ対象への別の参照を登録することもできる。
alias = app_a.settings.link_solution(
    external_config, ProjectType.STATIC_LIBRARY, name="Common"
)
app_a.settings.unlink(a.dependency_id)  # app_bが使用中なのでGUID名の登録を保持
app_b.settings.unlink(b.dependency_id)  # 最後の利用なのでGUID名の登録を自動削除
app_a.settings.unlink(alias.dependency_id)  # Commonも自動削除
```

名前は大文字・小文字を区別する空でない文字列。同名・同じ対象GUID・同所在なら共用する。同名の異なる対象や、同じGUIDの異なる所在はエラー。同じProjectに同名・同種類を二度追加することはできない。別名で同じ対象を追加しても、ビルド・全体.slnではGUIDと種類に基づき一つに集約する。単一の利用Projectから同じ対象の異なる種類をリンクすることは従来どおり不可。

GUIDは `project.settings.get().guid` で取得できる。`add_project` は渡した設定のGUIDを引き継がず、新しいGUIDを発行する。Project移動はGUIDを保持するので、既存リンクは移動後も同じProjectを参照する。テンプレートから新規作成した所属Projectは新しいGUIDになるが、外部への参照は維持する。

Projectの登録解除でも未使用になった参照を自動削除する。依存の変更を生成物へ反映する際は、従来どおりupdate/buildを呼ぶ。リンク先のファイルや成果物は削除しない。

旧管理ファイルはopen/reload時に自動保存して移行するため、初回は書き込み権限と旧外部リンク先が必要。外部Solution全体の所在を変えた場合は、全利用元でunlinkして新しい所在へ再リンクする。GUIDだけで移動先を探索する機能はない。
