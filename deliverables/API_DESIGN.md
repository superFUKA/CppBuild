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

追加確認済み（実装再開時）：成果物はProjectごと・構成条件ごとに分離し、依存先の成果物を参照する。
全体／個別操作では同じProject成果物を使い、全体用の複製は作らない。必要な依存先を先にビルドする。
AppのcleanでMath・Toolを削除しない。この合意は以下の全体統合・別生成範囲に関する旧未確定記述より優先する。

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

確認済み：Solution.openには全体の設定ディレクトリ.cppbuildを渡す。全体設定はSolution配下、個別設定は各Project配下に置く。共有素材は全体の.cppbuild/templates/。APIに渡す相対パスは全体設定がSolutionルート、個別設定・ファイル操作・リンク先がProjectルート、open/createが呼び出し側の作業ディレクトリを基準とする。2026-09-30変更：保存JSONのパスは各設定ファイルの親ディレクトリ基準の相対パスに変換する。JSON読み込み時はAPIの基準へ戻す。パッケージ名・システムヘッダー名はファイルパスと区別する。

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
| solution.move_project(name, destination, *, auto_update=True) | 同一Solution内でProjectフォルダーと登録先を移動。FileOperationReportを返す |

2026-09-28実装：`move_project`のdestinationはSolutionルート基準（配下の絶対パスも可）。既存出力先・管理領域・他Projectや移動元との同一／入れ子配置・ファイルシステムリンクを拒否する。Project名、主Project、内部依存ID、既存Project/Settingsオブジェクト、非保存設定、イベント登録を保持する。管理データ内の型付きパスとGoogleTest ZIP・明示ツールパスを補正する。元から存在しないProjectの移動済みパス修復、別Solutionへの移管、名前変更は含めない。

移動したProjectの全種類・全アーキテクチャのgenerated/buildを全体`.cppbuild/relocations/<id>/`へ退避し、個別管理設定を保持する。保存やrenameの失敗は設定・ディレクトリ・キャッシュを復元して例外を送出する（プロセス強制終了・復旧I/O自体の失敗までの原子性は保証しない）。他操作を停止して呼び出す。検出した設定競合・実行中run・操作ロック・コールバック内呼び出しは拒否する。

既存のファイル操作と同じ`FileOperationReport`を返す。changed_pathsは旧／新Projectパス、更新設定、キャッシュ退避先を含む。auto_update=Trueは移動確定後にsolution.updateを呼び、updateには全体のOperationReportが入る。再生成失敗は移動を取り消さず、success=False・pending_update=Trueとupdateまたはupdate_errorで通知する。FalseはCMakeを実行せずsuccess=True・pending_update=True。移動自体は設定操作としてfile_changedを発火せず、自動更新時は既存のbefore_update/after_updateを発火する。利用スクリプト・C++本文・任意の引数や環境変数・他Solutionの設定は書き換えない。

基本操作は確認済み。空Solutionのmain_projectはNone。最初の追加で自動設定し、以後はsolution.settingsで変更する。テンプレート指定時は構成・主Projectを引き継ぐ。Solution自体の種類引数は不要。

主Project・参照中Projectは先に参照を解決してから解除する方針。解除の詳細、既存Project取り込み、複数種類の作成時表現は未確定。独立したProject.open、全体削除・独立した復元APIは追加しない。

<a id="templates"></a>

## ジャンル2：テンプレート

2026-09-18 M6実装補足：以下の未確定記述に対し、現在の実装選択は次のとおり。設計上の合意と実装選択を区別する。
`TemplateTools.create_solution_template`は作成先の絶対`Path`を返し、`Solution.create(..., template=path)`が復元する。保存済み設定・ソース・素材をコピーし、非保存のビルド設定・イベント・観測状態は引き継がない。`.cppbuild`の生成物、`.git`、`build`、`dist`、`.test-work`、`__pycache__`、`.venv`、`.cache`は除外し、管理設定と共有素材は別途復元する。既存出力先・元との入れ子・ファイルシステムリンクを拒否する。外部参照は同じ対象を維持するよう最終出力先からの相対パスへ補正し、配布用に内包しない。
共有素材は`.cppbuild/templates`配下に保存し、`settings.set_file_template(name, path)`で登録、`file_templates()`でコピー取得、`remove_file_template(name)`で登録のみ解除する。置換はUTF-8本文だけを対象に一度行い、展開時のキーは`{{name}}`と完全一致が必要。置換を省略するとバイナリもそのままコピーできる。コード・ファイル名の推測置換やエスケープ構文はない。

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

M6実装：`info()`は`SolutionInfo`とProjectごとの`ProjectInfo`を返す。設定はコピー、ファイルと成果物は直近の観測値。`generation_state`はunknown/current/stale/failed、`build_state`はこれらにcleanedを加える。再open直後はunknown。API経由の変更と設定差分で古さを判定し、外部ファイル変更は走査しないため`external_changes_checked=False`を返す。`last_operation`と`last_success`は直近操作の結果で、非同期run未完了時はsuccessがNone。成果物一覧は存在を再検証した一覧ではない。

詳細案：SolutionSettingsDataは所属参照・main_project・共有テンプレート等、ProjectSettingsDataは対応種類・ソース・依存・PCH・種類別設定への参照等を持つ。実行用のSolutionBuildSettings／ProjectBuildSettingsは含めず、saveの対象にしない。全体saveで個別設定を上書きしない。所属の変更はadd/removeを通す。

reload時のProject参照維持・解除済み参照の無効化・外部編集との競合は詳細案。未取得・古い情報をinfoで区別する。project.infoやproject.on/offは対称性だけを理由に追加しない。

set_project_settingsやset_source_directoriesは追加せず、個別settingsのデータ変更を使う。C++規格・出力先・VSプロパティ相当の設定はデータ項目として設計し、項目ごとの専用API確認はしない。

ProjectTypeはSTATIC_LIBRARY・SHARED_LIBRARY・EXECUTABLE・INTERFACE_LIBRARY・TEST。ライブラリは静的・共有・インターフェースの3形式を常に持ち、ProjectBuildSettings.project_typeで相互に切り替える。EXECUTABLEとTESTは各形式固定で、他形式との混在・変換を拒否する。旧名称HEADER_ONLYは廃止した。INTERFACE_LIBRARYは.cppが存在してもコンパイルせず、インクルードパス・公開定義・依存関係を利用側へ渡し、自身のライブラリバイナリを生成しない。

ProjectSettingsData.initial_typeは作成時形式を保存する管理情報で変更不可。add_projectのproject_typeで設定し、ProjectBuildSettings.project_type省略時に使用する。非保存のビルド時選択は再open後に復元しない。作成・旧設定移行では不足するライブラリ形式へ既定のTypeSettingsDataを補う。種類別設定は相互コピーしない。saveでライブラリ3形式の一部を削除することはできない。依存はリンク時に指定した種類を要求し、提供側の単独ビルド時選択には追随しない。ソースを自動変換せず、各形式への対応は利用側の責任とする。

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
| project.settings.link_solution(config_directory, link_type) | 別Solutionの主Projectへリンクし、GUIDによる参照を所属Solutionへ自動登録 |
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

2026-09-30 clean修正：APIと引数は維持し、事前のCMake構成・再生成を廃止した。既存の所有ビルドツリーに対して現在のProject設定でMSBuild Cleanを実行し、全体.slnの生成・ソース走査・対象の外部依存解決は行わない。全体cleanはbuild_projectsを使用し、対象外Projectの依存解決で共有成果物を保護する。保護範囲を確認できない場合は削除せず失敗結果を返す。未生成・空のビルドディレクトリ、空の対象選択は成功。所有不一致・未所有・キャッシュ／対象.vcxprojの欠落は失敗結果。処理なしの成功・失敗・保護によるスキップはcommand=()、returncode、outputを持つProcessReportをOperationReport.processesへ格納する。管理設定の読込・検証エラーは従来どおり例外。キャッシュ・VSプロジェクト・保存設定は削除しない。rebuildはビルド用の生成後にCleanとbuildを行う既存動作を維持する。

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

2026-09-18 M5実装補足：`project.test()` / `solution.test()`と`TestReport`を追加した。以下の過去の未検証記述は設計時点の記録で、最新の実検証は[実装記録](IMPLEMENTATION_STATUS.md)を参照。
非保存の`SolutionBuildSettings.test_projects`で独立選択し、`None`はTEST種類の全所属（名前順）、`[]`は対象なしを表す。明示一覧は指定順。個別testは全体選択に依存しない。非TESTへのtestとTESTへのrunは拒否する。
`TestReport`は`processes`・`cases`（name/status/output）・`diagnostics`・全体操作時の`projects`を持つ。0件、全件skip、結果XMLの欠落・破損、工程失敗を成功扱いにしない。取得できたケースと前工程の結果は保持する。
GoogleTest 1.14.0 ZIPとSHA256を固定し、FetchContentで展開・組み込む。非保存の`ProjectBuildSettings.googletest_archive`で同じZIPのローカルパスを渡せる。省略時は公式GitHub URLから取得する。2026-09-18に許可後のオンライン取得と統合利用例のテスト成功を確認した。

M5の実行設定：Solutionの`parallel`は全体MSBuildの並列数、`run_parallel`は同時実行数（既定1）、`run_wait`は終了待機（既定True）、`run_continue_on_failure`は失敗後継続（既定False）、`test_continue_on_failure`はProject間のテスト失敗後継続（既定True）。Projectの`test_parallel`はCTestの`-j`（既定1）、`run_wait`は個別runの終了待機。全体runはSolutionの制御設定を使う。
runは必要なビルドを同期実行後、既定では`OperationReport`を返す。`run_wait=False`では`RunReport`を返し、`done`と`wait(timeout=None)`で完了を確認できる。未完了時の`success`はNone。ビルド失敗時はアプリを起動せず`OperationReport`を返す。
並列runは対象一覧を最大`run_parallel`件ずつ処理し、各組の完了後に次の組へ進む。結果の並びは対象一覧順で、並列時のOS上の起動・終了順は保証しない。停止設定は次の組に適用し、既に起動したアプリは終了を待つ。非同期runはPython終了から切り離す機能ではなく、終了コード・出力はwaitで回収する。非同期実行中の成果物に対するclean/rebuildは利用側でwait後に行う。

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

M6実装：`Environment.check(options=None)`は`EnvironmentOptions`（tools/configuration/architecture/cpp_standard/require_ctest）を受け取る。`EnvironmentReport.items`の各`EnvironmentItem`はname/path/version/success/detail/actionを持ち、追加Projectの結果は`projects`で返す。CMake・必要時のCTestを確認し、一時領域で指定構成のVS2022 C++構成・ビルドを実行する。依存のパスとローカルGoogleTest ZIPのハッシュもowner経由の診断で検査する。パッケージ内targetの使用可否は実buildで確認する。
`ToolSettings(cmake="cmake", ctest="ctest", environment={})`は実操作と診断で共用する非保存設定。実行ファイル名または絶対パスを指定し、environmentは子プロセスだけに適用する。Projectのtools既定は親継承。全体生成・ビルドではconfiguration/architecture/toolsの一致を要求する。

Environment.check(options)はSolutionを開く前の確認。solution.check_environment()／project.check_environment()は実操作と同じ設定・ツール条件で確認する。旧options引数の整理は実行時データ設計へ残す。

確認パス・バージョン・不足・必要な対応をデータで返し、表示は利用側が行う。自動インストールは対象外。具体項目・結果型・Environment.checkの引数省略可否は未確定。

<a id="events"></a>

## ジャンル11：イベント

M6実装：`file_changed`と、update/build/rebuild/clean/run/testの`before_*`・`after_*`をサポート。`Event`はname/solution/project/changed_paths/result/errorを持つ。全体操作はproject=None、個別操作は対象Projectを通知する。内部依存の処理ごとには別イベントを発火しない。非同期runのafterは起動処理の戻り時であり、完了確認はRunReport.waitを使う。
コールバックは登録順。同じ登録の再帰通知を抑止し、通知中の追加登録は次回から、解除は直ちに反映する。深さ32で残りの通知を打ち切り、エラーとして報告する。file_changed内のファイル操作は許可し、最外周の通知後に変更先Projectを初出順で各1回更新する。更新はauto_update指定の論理和で決め、失敗してもファイル変更は保持してFileOperationReportへ記録する。ネストしたファイル操作の戻り値は全体の更新完了を表さないため、最外周の結果を確認する。
コールバック内からのupdate/build/rebuild/clean/run/testの再入、およびfile_changed以外からのファイル変更は拒否する。beforeの失敗は本処理を中止しEventCallbackError、afterの失敗は実行結果を保持したEventCallbackErrorとなる。本処理の例外はafterのerrorへ渡してから再送出する。イベント登録は保存しない。

solution.on(event, callback)は登録IDを返し、solution.off(registration_id)で解除する。Python関数はSolutionインスタンスの有効期間内だけ保持し、専用ファイルや関数の保存を要求しない。

個別操作からも所属Solutionの登録へ通知し、対象Project・変更パス・結果等を渡す。イベントはファイル変更後・ビルド／テスト前後等が候補。連動して追加したテストファイル等は変更先Projectごとにまとめて更新する案。再帰制御・順序・失敗・更新のまとめ方は未確定。auto_update既定ONは維持する。

## 次の設計判断

全体／個別の生成範囲、実行用設定の保持・保存との接続、cleanと共有依存を[BUILD_DESIGN.md](BUILD_DESIGN.md)に具体案として記載した。未合意の案を確認済みに変更しない。追加確認はジャンル単位で行い、実装はまだ開始しない。
## 2026-09-29追加：全体.slnのフォルダー配置

`SolutionSettingsData.solution_folders: SolutionFolderSettings | None` を保存対象として追加。既定値は `None`（従来表示）。

`SolutionFolderSettings(projects="Projects", linked_projects="LinkedProjects", project_folders={})` は、自分の所属Projectのルート名、外部Solution群のルート名、所属Project名から相対表示階層への対応を持つ。既存の `solution.settings.get()/save()` と `solution.update()` で設定・反映する。所属やリンクの登録APIは変更しない。

有効時は外部Solutionの全所属Projectを生成・表示し、ビルド参加は従来の所属・依存関係を維持する。パスの制約・種類選択・同名解決は [利用手順](USAGE.md) を参照。

## 2026-09-30追加：GUIDと外部リンクの自動管理

- 2026-09-30の追加指示により名前指定を廃止。`link_solution(config_directory, link_type)` の2引数で使用し、`name` 引数は受け付けない。同じ対象GUID・同所在は自動で共用し、同じGUIDの異なる所在は拒否する。ただし、ビルドなどの依存解決では操作を始めた最上位Solution（Solution操作ではそのSolution、Project操作では所属Solution）の参照一覧に登録されたGUIDは、入れ子の依存先が自分の参照一覧で別の所在に登録していても、最上位の所在を優先して使用する。同一Project内の同GUID・同種類の再登録はエラー。ビルド対象もGUIDと種類で集約する。
- `ProjectSettingsData.guid` は作成時に生成する不変の識別子。作成設定を流用しても新しいGUIDを発行する。`SolutionSettingsData.references` は対象GUIDから `ProjectReference(project_guid, solution_directory)` への自動管理一覧で、キーと値のGUIDは一致する。`solution.settings.get().references` からコピーを取得でき、通常のSolution設定saveでの一覧変更は拒否する。
- 内部・外部とも `Dependency(project_guid, project_type)` を保存する。対象名・参照名・外部パスをProject側の依存に保存しない。外部対象の所在はSolutionの参照一覧から取得し、型付きパスは設定ファイル基準の相対パスで保存する。
- `unlink(dependency_id)` は利用元Projectの依存を解除し、最後の利用がなくなった対象GUIDの登録を自動削除する。Project登録解除やProject設定saveによる依存削除も同じ規則。対象ファイルは削除しない。事前登録・手動削除のAPIは追加しない。
- 同一Solution内の `link_project` は引数を維持し、内部の対象識別にGUIDを保存。外部リンクは登録時の主ProjectをGUIDで固定し、以後の主Project変更で対象を切り替えない。`LinkReport` と解除用IDは維持する。
- 旧ファイルはopen/reload時にschema_version=4へ移行する。名前付き参照は保存済みGUIDへ統合し、解除用IDは保持する。初回は書き込み権限が必要。対象GUIDが未保存の旧外部リンクにはリンク先へのアクセスも必要。移行済みファイルの再読み込みは保存しない。詳細は [設定詳細](SETTINGS_DESIGN.md) を参照。

## 2026-09-30追加：設定ファイル基準の相対パス

現行の管理JSONはschema_version=4（生成器切り替えでは変更なし）。バージョン2の相対パス形式を維持し、所属Project・共有素材・ソース・PCH・includeディレクトリ・種類別ファイル・外部Solution・CMakeソース／パッケージ・ImportedLibraryの型付きパスを各ファイル基準で相対保存する。絶対パス入力も保存時に相対化する。別ドライブ／別共有など相対化不能な指定はSettingsError。旧schema_version=1は元の配置でopen/reloadすると自動移行する。移設時には利用側と参照先の相対配置を維持し、環境依存の生成物・CMakeキャッシュを再生成する。

## 2026-09-30追加：生成器・コンパイラの切り替え

ユーザー合意：VS固有の操作以外をWindows限定にしない。生成器の省略時はOSごとの固定値。本ライブラリがVSを前提にしていた箇所はすべて修正対象。cleanはNinjaの挙動（CMake標準のclean）へ統一。検証は手元の環境で行う（GitHub Actionsは当面なし）。

- 非保存のビルド設定 `CMakeSettings(generator=None, toolset=None, c_compiler=None, cxx_compiler=None, toolchain_file=None)` を追加。`SolutionBuildSettings.cmake` の既定は `CMakeSettings()`、`ProjectBuildSettings.cmake` の既定は `INHERIT`（オブジェクト単位で継承）。管理JSONへは保存しない。
- `generator=None` は、Windowsでは `Visual Studio 17 2022`、その他では `Ninja Multi-Config`。対応は `Visual Studio 17 2022`／`Visual Studio 18 2026`／`Ninja Multi-Config` で、他はSettingsError。
- VSでは `c_compiler`/`cxx_compiler` を指定不可。`toolchain_file` は絶対パスで、コンパイラ指定とは排他。コンパイラは名前か絶対パス。`toolset` はVSでは `-T`、Ninja＋MSVCではMSVCのバージョン（vcvarsallの `-vcvars_ver`）。
- `architecture` の既定は `"x64"` から `None`（ホスト／コンパイラの既定）に変更。値は `None`/`x64`/`Win32`/`ARM64` で、全生成器で有効。Windows＋Ninja＋MSVCでは、vswhere／vcvarsallで対象のMSVC環境を用意する。
- `UpdateReport` に `generator` と `compiler`（`CompilerInfo`：id/version/path/architecture）を末尾に追加。VS以外ではsolution_file/project_file/filters_fileはNone。VS2026のsolution_fileは.slnx。全体updateのartifactsは、VSでは全体のソリューションファイル、それ以外では空。
- `EnvironmentOptions.cmake` を追加し、`EnvironmentOptions.architecture` の既定を `None` に変更。`EnvironmentReport` は解決後のarchitectureと `generator` を返す。診断項目名は、既定のVS2022では従来の `vs2022`、それ以外では `compiler`。`Environment.generators(tools=None)` はCMakeの生成器ごとに `GeneratorInfo(name, platform_support, toolset_support, supported)` を返す。
- cleanは全生成器で、所有ツリーに対して `cmake --build --config <構成> --target clean` を実行する。ツリー内部のGoogleTest・CMakeSourceも削除対象になる。事前の構成禁止と共有依存の保護は維持する。
- 依存先の成果物は、生成するCMakeListsが出力する `cppbuild-outputs-<構成>.txt`（TARGET_FILE／TARGET_LINKER_FILE）で特定する。拡張子で判定しない。
- 詳細・制約は [利用手順](USAGE.md) の「生成器・コンパイラの切り替え」、検証状況は [実装記録](IMPLEMENTATION_STATUS.md) を参照。
