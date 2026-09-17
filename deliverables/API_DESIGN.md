# 利用側API設計

> 2026-09-17 実装開始後の追記：M1（管理モデル・設定保存・非保存ビルド設定）を実装し、自動テスト13件を確認。最新の進捗・検証・次の作業は[実装記録](IMPLEMENTATION_STATUS.md)を参照。以下の「未着手」「実装はまだ開始しない」は設計整理時点の記録であり、今回の実装依頼を制限しない。設計の合意状態は維持する。

更新日：2026-09-17。設計資料。実装・自動テスト・CMake実ビルド検証は未着手。

## 合意状態と資料の役割

- **確認済み**：ユーザーと確認した責務・基本操作。全フィールドや具体型の確定を意味しない。
- **詳細案／再提案**：合意を実現する具体案。今回提示した入口・保存形式・生成範囲を合意済みと扱わない。
- **未確定**：追加設計・検証が必要な事項。
- **暫定採用**：現時点の案で進めることを了承済み。後からの見直しを許容し、動作検証済みという意味にはしない。

最新合意：SETTINGS_DESIGN.mdの親設定継承・実行順の既定・固定版FetchContentによるGoogleTest導入は暫定採用とする。以下に「案」「未合意」と残る同事項の記述より、この合意を優先する。具体的な型・版・配置等は実装時に整理し、利用方法に影響する問題があればまとめて報告する。

本資料は現行の公開API一覧。要件は[FEATURE_REQUESTS.md](FEATURE_REQUESTS.md)、更新・設定・成果物管理の具体案は[BUILD_DESIGN.md](BUILD_DESIGN.md)、判断事項と検証状況は[DESIGN_NOTES.md](DESIGN_NOTES.md)。旧記述は[整理前の記録](../references/DESIGN_BEFORE_DETAIL_2026-09-17.md)に保存した。

## 共通構成

確認済み：CMakeをラップするPythonライブラリ。公開名称はVS2022に合わせてSolution（全体）／Project（個別）とする。CMake内部のtargetはこの公開名称とは別に使う。updateはVS2022の.sln・.vcxproj・.vcxproj.filtersの生成・更新を前提とし、コンパイルしない。

| オブジェクト | 責務 |
| --- | --- |
| Solution | 所属Project、全体のビルド・実行対象、共有テンプレート、イベント、全体情報 |
| Project | 個別のビルド対象、詳細ビルド設定、ファイル操作、依存・PCH |
| solution.settings / project.settings | 保存対象の設定のget・save・reload |
| SolutionBuildSettings / ProjectBuildSettings | 各オブジェクトへ渡す構造化したビルド設定データ（型名は案） |

構造化したビルド設定をメソッドで渡し、操作はその設定を使う。ビルド設定はメモリ上だけに保持し、外部設定ファイルには保存しない。保存・読み込みするのはSolution／Projectの管理設定と種類別の管理設定。「普段」と「今回だけ」の使い分けは利用側が決める。旧モデルの一時マージやビルド設定の保存・復元は採用しない。[設定詳細](SETTINGS_DESIGN.md)を参照。

以下のメソッド名は名称変更に合わせた整理案。元の基本操作の合意を維持するが、改名後の細かな綴りまで新たに確認済みとしない。

| 旧名称 | 現行の名称案 |
| --- | --- |
| Project / Target | Solution / Project（クラス名は確認済み） |
| get_target / targets / add_target / remove_target | get_project / projects / add_project / remove_project |
| TargetType / main_target | ProjectType / main_project |
| target.settings.link_project / link_target | project.settings.link_solution / link_project |
| TemplateTools.create_project_template | TemplateTools.create_solution_template |
| ProjectSettingsData / TargetSettingsData | SolutionSettingsData / ProjectSettingsData |

### パスと保存

確認済み：Solution.openには全体の設定ディレクトリ.cppbuildを渡す。全体設定はSolution配下、個別設定は各Project配下に置く。共有素材は全体の.cppbuild/templates/。全体の相対パスはSolutionルート、個別設定・ファイル操作・リンク先はProjectルート、open/createの引数は呼び出し側の作業ディレクトリが基準。パッケージ名・システムヘッダー名はファイルパスと区別する。

詳細案：全体の入口を.cppbuild/solution.json、個別を各Projectの.cppbuild/project.jsonとする。旧全体設定名project.jsonからの変更であり、未合意。保存形式のschema_versionとパスの検証方法はBUILD_DESIGN.mdを参照。

### 設定・結果・失敗

確認済み：getは独立したコピー、saveは検証・保存・管理オブジェクトへの反映、reloadは保存済みデータの読み直し。設定操作だけでCMakeを実行しない。保存失敗時にメモリだけ新設定にしない。

詳細案：引数・設定・I/Oエラーは例外、起動済み外部プロセスの非ゼロ終了は結果で返す。部分成功を保持する。具体的な結果フィールド・例外型は未確定。

| 結果型 | 内容・状態 |
| --- | --- |
| FileOperationReport | ファイル変更、更新有無・結果、未反映状態。基本構成は確認済み |
| UpdateReport | 成功可否、認識ファイル、生成結果、ビルドディレクトリ。生成.sln等のパスと更新範囲を加える案 |
| LinkReport | 依存ID、対象、種類、変更内容。解除用IDを返すことは確認済み |
| ChangeReport | 作成・変更・削除、診断、未反映状態（詳細案） |
| ProcessReport / OperationReport / TestReport | 工程、終了コード、出力、テスト結果等。具体型・フィールドは未確定 |

ログ通知・キャンセル・ツールパス・子プロセス環境は実行時データで受け取る案。親プロセス環境は書き換えない。

<a id="project"></a>

## ジャンル1：Solution・Projectの基本操作

| API案 | 責務 |
| --- | --- |
| Solution.create(destination_directory, solution_name, *, template=None) | 空またはテンプレートからSolutionを作成して返す |
| Solution.open(config_directory) | 所属Projectと必要設定も自動で読み込む |
| solution.get_project(name) / solution.projects() | 個別取得／一覧 |
| solution.add_project(directory, name, project_type, settings=None) | 名前・種類を直接指定しProjectを追加。settings省略時は既定設定 |
| solution.remove_project(name) | 所属登録のみ解除し、ソース・個別設定は残す |

基本操作は確認済み。空Solutionのmain_projectはNone。最初の追加で自動設定し、以後はsolution.settingsで変更する。テンプレート指定時は構成・主Projectを引き継ぐ。Solution自体の種類引数は不要。

主Project・参照中Projectは先に参照を解決してから解除する方針。解除の詳細、既存Project取り込み、複数種類の作成時表現は未確定。独立したProject.open、全体削除・独立した復元APIは追加しない。

<a id="templates"></a>

## ジャンル2：テンプレート

| API案 | 責務 |
| --- | --- |
| TemplateTools.create_solution_template(solution, destination_directory) | 設定・ソース・素材をコピーしテンプレート参照を返す |
| solution.create_file_template(name, source_file, *, replacements=None) | 素材作成・登録・保存をまとめて行う |
| solution.settings.set_file_template(name, template_file) | 既存素材の登録・同名登録の更新 |
| solution.settings.file_templates() / remove_file_template(name) | 登録情報の取得／解除。解除は素材を残す |

Solutionテンプレートは各Project設定を含み、ビルド成果物を除く。作成時に自動反映する値はsolution_nameだけ。任意のコード・名前の推測置換、独自variables・生成スクリプト登録は対象外。利用者にCMakeの手書きを求めない。参照型と戻り値の詳細は未確定。

ファイルテンプレートはSolutionで共有し、Projectへの追加に使う。素材作成は同名登録・保存先衝突をエラーとし、元ファイルを変更しない。作成時のreplacements={"class_name": "Example"}でExampleを{{class_name}}へ、追加時の{"class_name": "Player"}でPlayerへ置換する。省略すればコピー。エスケープ・不足値・置換範囲・複数ファイル・パス名の置換は未確定。

単一Projectのテンプレート作成・利用は対象外。種類に応じた標準初期構成と、独立種類TESTの標準化は維持する。

<a id="settings"></a>

## ジャンル3：設定・情報

| API | 責務・状態 |
| --- | --- |
| solution.settings.get() / save(values) / reload() | 全体の設定管理。基本操作は確認済み |
| project.settings.get() / save(values) / reload() | 個別の設定管理。基本操作は確認済み |
| solution.settings.set_build_profile(profile) | ビルド設定を保存しない最新方針により、保存用APIとしては撤回。名前付き構成管理は利用側に委ねる |
| solution.info() | 設定・認識済みファイル・取得済み成果物・状態等。取得のための走査やCMake実行なし |

詳細案：SolutionSettingsDataは所属参照・main_project・共有テンプレート等、ProjectSettingsDataは対応種類・ソース・依存・PCH・種類別設定への参照等を持つ。実行用のSolutionBuildSettings／ProjectBuildSettingsは含めず、saveの対象にしない。全体saveで個別設定を上書きしない。所属の変更はadd/removeを通す。

reload時のProject参照維持・解除済み参照の無効化・外部編集との競合は詳細案。未取得・古い情報をinfoで区別する。project.infoやproject.on/offは対称性だけを理由に追加しない。

set_project_settingsやset_source_directoriesは追加せず、個別settingsのデータ変更を使う。C++規格・出力先・VSプロパティ相当の設定はデータ項目として設計し、項目ごとの専用API確認はしない。

ProjectTypeはSTATIC_LIBRARY・SHARED_LIBRARY・EXECUTABLE・HEADER_ONLY・TEST。複数種類への対応と、種類ごとに管理設定ファイルを持つことは確認済み。supported_types、ファイル名・項目・許容組合せは詳細案。登録だけで任意のソースが静的／共有の両方へ対応するとはしない。

確認済み：Solution／Projectへビルド設定をメソッドで渡す。具体名はsolution.set_build_settings(values)、project.set_build_settings(values)を案とする。保持した設定を操作時に使い、管理設定のsave/reloadでは保存・復元しない。コピー保持・全置換・未設定時の扱いは詳細案。

親Solutionの共通値に従う継承指定をProjectに設ける案。configurationやarchitecture等の適用可能な項目を解決し、子の明示値を優先する。独立したCMake構成間の継承はライブラリが解決する。継承は重複指定を減らすが、依存間の不整合を無条件に解決するものではない。継承指定の配置・型は未確定。

<a id="files"></a>

## ジャンル4：ファイル・ディレクトリ

| API | 責務 |
| --- | --- |
| project.add_file(destination, *, template_name, replacements=None, auto_update=True) | 共有テンプレートから追加 |
| project.add_file(destination, *, content, auto_update=True) | テキストから追加 |
| project.remove_file(path, *, auto_update=True) | 実ファイル削除 |
| project.move_file(source, destination, *, auto_update=True) | 移動・名前変更 |

基本構成とFileOperationReportは確認済み。add_fileの2行は同一メソッドの利用形。contentとtemplate_nameの排他・既存ファイル上書き拒否は案。空contentは空ファイル。remove_fileは再帰的ディレクトリ削除を含めない。include文の自動修正はしない。

auto_update=Trueで対象Projectの更新を行い、Falseはファイル変更・管理情報更新までで保留する。ファイルだけ成功し生成に失敗した場合を全体成功にしない。更新先の具体的な.sln範囲はBUILD_DESIGN.mdの未合意案による。表示対象とコンパイル対象は区別する。

<a id="cmake-update"></a>

## ジャンル5：VS2022ファイルの更新

最新の補足：updateの主目的は、実際のソースファイル配置をVSのプロジェクト内ファイル一覧・フィルター構成へ反映すること。ソースをフィルターに合わせて自動移動する意味ではない。project.updateでは対象Projectの走査・ソース一覧・フィルター定義の更新に範囲を絞る。solution.updateでは全所属Projectを扱う。ライブラリの走査範囲とCMakeの構成・生成範囲を区別する。

CMakeのtarget_sourcesで個別targetのソースを指定し、Projectごとに分けたCMakeディレクトリ内のsource_group(TREE ...)で配置に対応するフィルターを定義する案。source_groupはtarget直接指定ではなくディレクトリスコープである。

追加確認済み：各Projectを独立して構成・生成できるCMake入口とビルドディレクトリ・キャッシュを持たせ、project.updateはその生成環境を再構成・再生成する。この個別更新はCMakeの標準操作で実現する方針とする。単に全体CMakeの下位ファイルを分けるだけの構成とは区別する。

solution.update()とproject.update()の全体／個別の入口は確認済み。保存済み設定の再読み込み・検証、対象ファイルの走査、CMake入力の更新、VS2022ファイルの再生成まで行う。コンパイル・公開scan APIは不要。個別操作はそのProjectのビルド用CMakeを使う。

管理設定を外部編集した場合も、利用側が先にreloadする必要はない。getした管理設定コピーの変更はsaveするまで反映しない。ビルド設定は専用メソッドで渡し、updateの管理設定再読込では消去・復元しない。生成物・キャッシュから実行用設定を復元するAPIは設けない。旧update(options=None)の引数は再設計中。

個別更新では.vcxprojに加えて.filters、CMake管理ファイル、個別生成される.sln等も更新され得る。書き換えを.vcxproj一つに限定する保証はしない。個別生成した.vcxprojを全体.slnへ統合する方法・依存関係・構成対応・更新順序は詳細設計と実検証に残す。共通ツリー全体の再生成を個別updateの前提とする直前の提案は採用しない。全体で.vcxprojを別に再生成する旧案まで合意したものではない。

<a id="dependencies"></a>

## ジャンル6：依存・リンク

| API案 | 相手 |
| --- | --- |
| project.settings.link_solution(config_directory, link_type) | 別Solutionの設定ディレクトリから主Projectを選択 |
| project.settings.link_project(other_project, link_type) | 同じSolutionに所属するProjectオブジェクト |
| project.settings.link_package(package) | 外部CMakeパッケージの公開target |
| project.settings.link_cmake_source(source) | 既存CMakeソースのtarget |
| project.settings.link_imported_library(library) | ビルド済みライブラリ・ヘッダー等 |
| project.settings.unlink(dependency_id) | リンク登録解除 |

旧リンク入口を改名した案。相手を一つずつ登録し、前2つは第2引数に種類を渡す。非対応種類はエラー。解除用dependency_idを返す。リンク・解除は設定を保存し、update/buildで反映する。ファイル操作の自動更新をリンクへ拡大しない。

相手のmain_projectと種類から選ぶ。別の既定リンク先は増やさない案。リンク時の相手を保存して主Project変更で切り替えない、重複登録・依存の種類衝突を診断する案。別Solutionの主Project以外への直接リンクは必要性を確認する。

外部依存の対応条件は解除可能であること。インクルードパス等の付随設定が残ることは許容するが、解除済み依存の取り込み・リンクがビルドを妨げないこと。再読み込み後も解除可能にする。他で必要な依存を残し、依存先実ファイルの削除やアンインストールは行わない。詳細な引数型・キャッシュ副作用・失敗結果は未確定。

<a id="build-run"></a>

## ジャンル7：ビルド・実行

Solution／Project双方にbuild・rebuild・clean・runを用意する。操作は各オブジェクトに渡された設定を使う。全体の対象選択はSolution側、詳細はProject側。個別呼び出しは全体の対象選択に左右されない。選択外でも依存先として必要ならビルドする。

```python
# メソッドで渡す方針は確認済み。具体名・型・フィールドは詳細案。
app.set_build_settings(ProjectBuildSettings(configuration="Debug", cpp_standard=20))
solution.set_build_settings(SolutionBuildSettings(
    build_projects=["App", "Tests"],
    run_projects=["App"],
))
solution.build()
app.build()
```

build等にprofile・configuration・種類・argsをばらばらに並べない。従来のbuild_with_project／run_with_projectに相当する選択はSolution側へ移す。旧optionsによる一時上書きの呼び出し形は確定仕様としない。

個別build/rebuild/clean/testは個別のCMake・構成を使い、runは対応成果物を起動する。全体buildは全体のCMakeで対象と依存を扱い、単純に全Projectの同名メソッドを呼ぶとはしない。cleanで他のProjectや別生成範囲の成果物を消さない。

確認済み：実行順は既定の動作を用意し、設定で変更できるようにする。詳細案はbuildが依存順、runが対象一覧順の逐次実行・終了待機。順序・並列・待機・失敗後継続を設定可能とする案で、具体的な既定はSETTINGS_DESIGN.mdを参照。runの事前ビルド、実行引数と作業ディレクトリのデータ配置は引き続き詳細案。ライブラリをrun対象にしない。前工程失敗後に実行へ進まず、アプリの終了コードは意味を決めつけず返す。

<a id="tests"></a>

## ジャンル8：テスト

TESTを独立した種類としsolution.add_project(..., project_type=ProjectType.TEST)で作る。solution.test()／project.test()で必要なビルド後に実行しTestReportを返す基本構成は、GoogleTestで成立することを条件に確認済み。GoogleTest採用・取得方法・版・標準配置は未確定。実ビルド未検証。

全体のテスト参加はビルド・実行参加とは別に指定する。Solution設定へtest_projectsを置くことは今回の整合案であり未合意。Project単独のtestは全体参加に依存しない。

GoogleTestとCTestの登録・実行を使い、結果XML等をライブラリ内で読み取る案。テスト0件・結果取得不能を通常成功にしない案。TEST.runとtestの統一、非TESTへのtest拒否、命名・ラベル、失敗時の継続は未確定。テスト一覧がupdateだけで常に確定するとはしない。

暫定採用：CMakeのFetchContentで固定版を取得・組み込み、GTest::gtest_mainにリンクし、gtest_discover_testsでCTestへ登録する。TEST種類の生成CMakeへ組み込む。構成時に取得し得るがコンパイルはbuild時。具体版・配置は実装時に整理し、実検証は未実施。詳細はSETTINGS_DESIGN.md。

[GoogleTest公式例](https://google.github.io/googletest/quickstart-cmake.html)、[CMake GoogleTest](https://cmake.org/cmake/help/latest/module/GoogleTest.html)、[CTest](https://cmake.org/cmake/help/latest/manual/ctest.1.html)を設計上の参考とする。GoogleTestの自動ダウンロードを採用済みと扱わない。

<a id="pch"></a>

## ジャンル9：PCH

project.settings.set_pch(project_headers=..., system_headers=...)／clear_pch()で保存し、update/buildで反映する。ヘッダー作成にはadd_fileを使い、専用作成APIは設けない。解除でヘッダーを消さない。種類別PCH・戻り値詳細は未確定。

<a id="environment"></a>

## ジャンル10：環境チェック

Environment.check(options)はSolutionを開く前の確認。solution.check_environment()／project.check_environment()は実操作と同じ設定・ツール条件で確認する。旧options引数の整理は実行時データ設計へ残す。

確認パス・バージョン・不足・必要な対応をデータで返し、表示は利用側が行う。自動インストールは対象外。具体項目・結果型・Environment.checkの引数省略可否は未確定。

<a id="events"></a>

## ジャンル11：イベント

solution.on(event, callback)は登録IDを返し、solution.off(registration_id)で解除する。Python関数はSolutionインスタンスの有効期間内だけ保持し、専用ファイルや関数の保存を要求しない。

個別操作からも所属Solutionの登録へ通知し、対象Project・変更パス・結果等を渡す。イベントはファイル変更後・ビルド／テスト前後等が候補。連動して追加したテストファイル等は変更先Projectごとにまとめて更新する案。再帰制御・順序・失敗・更新のまとめ方は未確定。auto_update既定ONは維持する。

## 次の設計判断

全体／個別の生成範囲、実行用設定の保持・保存との接続、cleanと共有依存を[BUILD_DESIGN.md](BUILD_DESIGN.md)に具体案として記載した。未合意の案を確認済みに変更しない。追加確認はジャンル単位で行い、実装はまだ開始しない。
