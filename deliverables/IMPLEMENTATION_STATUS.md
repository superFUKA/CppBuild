# 実装・検証記録

## 2026-09-19：ライセンス追加

- ユーザーの「一般的なもの」という指定に基づきMITライセンスを採用。著作権表記は`2026 CppBuild contributors`。LICENSE、README、Pythonパッケージのライセンス情報とソース配布対象へ反映した。前項までのライセンス未指定状態は解消。
- wheelの再ビルド成功。内包LICENSEとリポジトリ本文の一致、METADATAのLicense-File、ソース配布用一覧への収録、差分の空白検査を確認。製品コードの変更はない。

## 2026-09-19：公開リポジトリの構成整理

- `.gitignore`にCMake/VS生成物、Python環境・キャッシュ、ローカルIDE/エージェント設定、`.env`を追加。ソース・テスト・利用例は除外しない。追跡済みの除外対象はなし。ローカル生成物は削除していない。
- `.gitattributes`で改行規則を指定。READMEをM5/M6完了後の内容へ更新し、CONTRIBUTING.mdに開発・検証・配布手順を追加。pyproject.tomlへREADMEを登録し、MANIFEST.inでソース配布物の範囲を明示した。
- 実装記録の個人環境の絶対パスを一般化。Git履歴は変更しておらず、過去コミットには元の記載が残る。
- 通常サンドボックスではpipの一時領域へのアクセスが拒否された。承認後の`python -m pip wheel . --no-deps --wheel-dir .test-work/oss-wheels`は成功。wheel内の全15モジュールとメタデータ、生成されたソース配布用ファイル一覧の包含・除外を確認した。ソースアーカイブ自体の生成と公開は未実施。製品コードを変更していないため実VS2022試験は再実行していない。
- LICENSEは未指定で、採用ライセンスと権利者表記をユーザーへ確認中。現時点ではOSSとしての配布条件が確定したとは扱わない。次の作業は指定されたLICENSE・パッケージのライセンス情報の追加と配布物の再確認。

2026-09-17。設計上の合意状態と、ここに記載する初期実装の選択は別。

## M1：管理モデルと保存境界

- 実装：`cppbuild/core.py`、`models.py`、`storage.py`。Python 3.11以上、標準ライブラリのみを使用。
- 確認：`python -m unittest discover -s tests -v`、13件成功。実ディレクトリへの作成・保存・open・失敗注入・競合・不正設定を含む。
- CMake/C++：この段階では起動しない。生成・実ビルドはM2。
- 自己レビュー：子の保存失敗、所属追加失敗、全体reload中の一部破損、外部変更、解除後の古い参照、全体saveの子への非干渉を検査・テスト化。
- AC：MILESTONES.mdのM1各項目を上記テストで確認済み。

初期実装の具体化（新しい「確認済み仕様」を意味しない）:

- 全体の入口は従来の`.cppbuild/project.json`を維持。`solution.json`への改名は未合意のため実施しない。文書種別でSolution/Projectを識別する。
- `schema_version=1`。種類別ファイルは`types/<kind>-<content hash>.json`。種類別ファイルを先に書き、最後に入口を原子的に差し替える。失敗時の未参照ファイルは残り得るが読込対象にはならない。自動GCは未実装。
- API名は現行資料の案を使用。設定データはdataclass。ビルド設定は独立コピーで全置換し、ProjectBuildSettingsのINHERITで親の値を解決する。
- 初期既定はDebug/x64/C++20。種類が複数ある場合は実行用データで明示選択を必要とする。複数種類の同時生成・許容組合せは未実装。
- 初期対象はSolution内の重複・入れ子でないProject。名前はASCII識別子、ソース範囲はProject内。外部Project取り込み、外部ソース、改名は拡張せず保留する。
- 保存競合は読み込み時の指紋と排他作成のロックで検出。ロックを無視する外部エディターとの完全な同時書き込み保証や、電源断時の複数所属ファイルのトランザクションは対象外。

## M2：独立Projectの生成・ビルド

- 実装：`cppbuild/engine.py`、Projectのupdate/build/run/clean/rebuild、内容指定のファイル追加・移動・削除。
- 検証環境：Windows、Python 3.12.10、CMake 4.2.3、Visual Studio 2022 Community 17.14。
- 自動テスト：`$env:CPPBUILD_TEST_VS2022='1'; python -m unittest discover -s tests -v`で25件成功（実VS2022試験3件を含む）。環境変数なしでは実ビルド試験のみ明示skip。
- 実ビルド：実行ファイル・static・DLL・header-only表示、Debug/Release切替、実行引数と終了コード、コンパイル失敗、実CMake構成失敗を確認。
- 非干渉：Appの更新でToolの.vcxproj内容が変わらず、App Debug clean後もApp ReleaseとToolの成果物内容が残ることを確認。
- ファイル追加・移動・削除のfilters反映、日本語・空白を含むパス、外部保存設定の再読込と実行用設定維持、設定不正時のプロセス起動抑止を検証。
- 自己レビューで明示指定されたbuildソース範囲の除外漏れ、事前検証失敗時に古い成功結果が残る問題を修正し、全25件を再実行して成功。
- AC：M2全項目を上記試験で確認。CMakeのコンパイラー検出試験と利用者のC++ソースのコンパイルは区別し、updateで後者を行わない。

初期実装の具体化と制約:

- Project配下の`.cppbuild/generated/vs2022-<architecture>-<kind>`と`.cppbuild/build/...`を専有。生成情報は実行用設定の復元元にしない。
- 固定のVS2022 generatorを使用。CMakeはPATHから起動する。ツールパス指定・環境診断はM6。
- 種類は一度に一つ選択。C++拡張子`.cpp/.cc/.cxx`だけをコンパイルし、それ以外の認識ファイルは表示対象とする。C言語・外部ソースへの拡張はしない。
- source_group(TREE)を使用。HEADER_ONLYはINTERFACEライブラリとソース表示用custom targetを組にする。
- 成果物はCMake File APIから取得。runは今回の設定で事前ビルドし、Projectルートを作業ディレクトリにする。
- cleanは依存を一切含まない専用ツリーに限ってCMakeのcleanを使用し、構成別の非干渉を実測した。**依存導入時にはこの実装をそのまま流用せず、M3の所有範囲検証・実装変更を必須とする。** 現段階のcleanも再構成を行うため有効なソース・設定を必要とする。
- ファイル変更後に構成が失敗してもファイル変更は保持し、FileOperationReportで未反映・失敗を返す。
- 標準初期ソースの自動作成、Solution全体のCMake生成、依存、PCH、TEST、テンプレート、イベントは未実装。実行可能な最小例は`examples/independent_project.py`。
- VS IDEのGUI表示は未確認。生成XML・実MSBuildによる確認と区別する。
- パッケージ：`python -m pip wheel . --no-deps --wheel-dir .test-work/wheels`でwheel生成を確認。非隔離ビルドはローカルsetuptools不足で失敗したため、宣言済みbuild-system依存を使う標準の隔離ビルドで再確認した。

参考：[source_group](https://cmake.org/cmake/help/latest/command/source_group.html)、[CMake File API](https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html)。実装ではローカルのCMake 4.2.3で上記機能を検証した。

## 後続

M3aでは独立.vcxproj統合候補の検証コードを実装した。
全体CMakeから外部targetを直接選択すると依存をビルドしない問題を再現し、
全体.slnをMSBuildへ渡すCMake custom targetで回避してDebug/Releaseの実ビルド・実行を確認。
個別更新後の全体利用、Tool非干渉、cleanと成果物共用も確認した。
自己レビューでは全体と個別の成果物を別物として扱っていないか確認し、共有を明示するassertionを残した。
手順・判断点・限界は[全体統合の判断資料](INTEGRATION_DECISION.md)を参照。
修正後の最終確認は同じ実VS2022有効化コマンドで全26件成功（実VS2022試験4件を含む、23.374秒）。

## M3b：内部依存と全体統合

最新合意に基づきProject別所有・全体／個別共用を実装。`graph.py`で依存を検証し、`solution_engine.py`で
独立生成.vcxprojを全体.slnへ登録する。全体CMakeのcustom targetから、CMakeが検出したMSBuildで
全体.slnの選択targetをビルドする。個別buildは依存順に各所有ツリーを処理する。

- link_projectはIDを返して保存、unlinkは再open後も可能。参照中Projectの登録解除を拒否する。
- 依存をIMPORTEDライブラリとして登録し、成果物は依存先のFile APIで解決。PUBLIC include・マクロ・推移的リンクを伝える。
- 個別updateは自分だけ走査・生成する。依存の生成情報が未取得なら「先に依存をupdate/build」を診断する。個別buildと全体update/buildは依存を先に準備する。
- 全体buildの既定選択は全所属（None）。明示空一覧はビルドなし。runの既定選択は空一覧。全体.slnの表示は全所属。
- 現段階の全体操作はconfiguration・architectureが一致する構成を対象とする。子の明示値は上書きせず、不一致をプロセス起動前に診断する。
- 個別cleanは自分の専有ツリーだけを対象にする。IMPORTED依存の成果物は削除しない。全体cleanは明示選択だけを対象とし、非選択Projectに必要な依存先を保護する。
- 構成変更・所属変更時は全体update/buildで全体.slnを再生成する。通常の個別ソース変更では全体.slnを再生成しない。
- APIでの選択ビルドを検証。IDEの既定ビルド参加は全所属のままであり、API側の選択との完全同期は保留。

検証：App→MathとToolによる公開API経由の全体／個別build/run、個別更新、Debug/Release、選択clean、
共有依存保護、unlink後の実ビルドを確認。自動テストは循環・不一致・非対応種類・登録解除制限も含む。
自己レビューでEXCLUDE_FROM_ALLが.slnの明示target処理を妨げる問題を修正し、対象選択はMSBuildの/tに限定した。
修正後の全32件成功（実VS2022試験5件、32.766秒）。M3bのACを確認した。

## M4：外部依存・複数種類・PCH

- CMakePackage、CMakeSource、ImportedLibraryを構造化データとして追加。対応するsettings.link_*で登録・保存し、再open後もIDで解除可能。
- packageは明示したディレクトリからCONFIGモードで取得する。sourceは所有Projectのビルドツリー内の専用binary_dirへadd_subdirectoryする。importedは構成別の実ファイルを参照し、対応構成がない場合に別構成へ暗黙フォールバックしない。
- link_solutionは登録時点の主Project名を記録する。後で主Projectを変えてもリンク先を切り替えない。外部Solutionのビルド条件は非保存のSolutionBuildSettings.external_build_settings（.cppbuildパス→設定）で明示でき、親を勝手に付け替えない。
- 静的／共有の同時要求は依存グラフで種類別ノードとして扱う。同一Projectの別種類を別のアプリから使える。一つの利用側から同じProjectの静的／共有の両方をリンクする衝突は診断する。
- PCHは共通管理設定のproject_headers/system_headersで保存し、set_pch/clear_pchから変更。HEADER_ONLYではコンパイルしない。ヘッダー削除を伴わない。
- cleanは対象.vcxprojのCleanとBuildProjectReferences=falseを指定。外部ソースのライブラリ・IMPORTED成果物を消さないことを実測した。
- 依存なしのM2からの差分を自己レビューし、外部Solutionの明示構成指定、種類衝突診断、推移的外部利用要件、解除後に古い外部CMakeを取り込まない処理を確認・修正。

実VS2022試験：別SolutionのDebug/Release、importedへの切替、外部source/packageの登録・解除、PCH生成と解除、
外部バイナリを保持したclean、static/shared同時生成と実行が成功。解除後は元の外部CMakeを意図的に失敗する内容へ変更しても利用側build/runが成功することを確認。
外部CMake自身の独自ダウンロード等の副作用の取消・アンインストールは行わない。
全35件の再実行成功（実VS試験8件、48.909秒）。さらに不正依存レコード・再帰した実行設定の回帰テストを追加して再確認。

M5〜M6は未完了。GoogleTest・テンプレート・イベントは後続。

## 2026-09-18：進捗確認と再検証

- Git履歴でM3a（bee1593）、M3b（2532840）、M4（febffc3）のコミットを確認。確認開始時の作業ツリーに変更なし。M1〜M4完了、M5〜M6未着手というマイルストーン記録と実装を照合した。
- Python 3.12.10 / CMake 4.2.3で、`CPPBUILD_TEST_VS2022=1`を設定して`python -m unittest discover -s tests -v`を実行。現在の収集数は追加の回帰テストを含め36件。
- 通常テスト28件は成功。実VS2022試験8件中、構成失敗を扱う1件は成功、残る7件はCMake構成時の`No CMAKE_CXX_COMPILER could be found.`で失敗。unittestの表示はサブテストの失敗を含め`failures=10`、所要9.912秒。
- 今回の環境ではC++実ビルド成功を再確認できていない。過去の35件成功記録は当時の結果として維持する。コンパイラー検出失敗の根本原因は未特定で、製品の回帰かどうかもこの結果だけでは判断しない。
- 次の作業：現在の実行環境でVS2022コンパイラー検出を確認し、実ビルド試験を再実行する。その後の開発対象はM5（GoogleTest / CTest・実行制御）、続いてM6（テンプレート・イベント・診断・利用例）。今回、実装コードの変更は行っていない。

## 2026-09-18：M5実装とオフライン実検証

- VS2022検出失敗の原因は生のプロセス環境に`Path`と`PATH`が重複し、MSBuildが`MSB6001` / `ArgumentException`で失敗することだった。Pythonの環境辞書を子プロセスへ明示し、親プロセス環境を書き換えずに正規化。既存36件が実VS2022試験を含め49.000秒で成功した。
- TEST種類のCMake生成、固定GoogleTest 1.14.0のFetchContent・gtest_main・PRE_TEST列挙、Project.test / Solution.testを実装。GoogleTestは各Project内で所有し、updateではコンパイルしない。
- `ProjectBuildSettings.googletest_archive`は非保存のローカルZIP指定。省略時の公式URLと同じSHA256（`1f357c27ca988c3f7c6b4bf68a9395005ac6761f034046e9dde0896e3aba00e4`）をCMakeで検証する。今回の実検証では既存の`固定版1.14.0.zip（個人環境の絶対パスは公開整理時に省略）`を読み取り専用で使用した。
- オンライン取得はこの環境のネットワーク制限で失敗。ユーザーから「後で許可するのでそれ以外」を進める指示があり、以後の通信取得は保留。ローカルZIP展開・ビルド・実行の成功とオンライン取得成功は区別する。
- CTestの構成指定・並列数を明示し、操作ごとに新しいJUnitファイルを読み取る。0件・全skip・XML欠落／破損・ビルド失敗を成功扱いにしない。全体testは独立した参加一覧・指定順・失敗後継続を持ち、個別testは全体選択に影響されない。
- runの並列数・待機・失敗後継続、全体ビルドの並列数を追加。非同期時はRunReportのdone / wait / success（完了前None）で追跡する。ビルド工程は同期実行し、失敗時はアプリを起動しない。並列起動は一覧順の組単位で処理し、結果の順序を維持する。
- 自己レビュー：保存されない設定の検証、対象重複・非対応種類、結果ファイルの鮮度、非同期出力回収、DLL検索環境、日本語パスでのテスト列挙、全体.sln経由のTESTビルドを確認。日本語パスとCMake 4.2のJSON列挙の組合せを修正し、無関係な複数種類ライブラリを既定のtest選択が解決しないようにした。
- 全46件を82.927秒で成功（通常36件、実VS2022試験10件）。実試験ではGoogleTestのDebug/Release、共有ライブラリ依存、全体build、成功・失敗・0件・コンパイル失敗、公開run APIの順序・継続・並列・非同期待機を確認。主な警告はテスト用一時ディレクトリへのビルドに対するMSB8029。VS IDEのGUIは未検証。
- 再実行時は`CPPBUILD_TEST_VS2022=1`と`CPPBUILD_TEST_GTEST_ARCHIVE=<固定版ZIPの絶対パス>`を設定し、`python -m unittest discover -s tests -v`を使う。テスト専用のM3a検証コードも生の環境重複を避けるため、今回は`subprocess.call([...], env=dict(os.environ, CPPBUILD_TEST_VS2022='1'))`でunittestを起動した。
- 利用例：`examples/google_test.py`。API・設定資料・設計メモへ具体フィールドと制約を反映。M5の実装とオフライン実検証は済んだが、オンライン取得再確認とコミットは残る。この環境では`.git`が読み取り専用のためコミットを実施できない。
- 最後の選択処理修正後にM5通常テスト8件を再確認（実試験2件はこの実行ではskip）。さらに`python -m examples.google_test .test-work/m5-example <固定版ZIP>`で生成・実ビルド・CTestを実行し、`Smoke.Arithmetic passed`を確認。差分の空白検査も成功。
- 次の作業：許可後にオンライン取得を確認し、書き込み可能なGit環境でM5をコミットする。後続開発はM6（テンプレート・イベント・環境診断・info・利用例）。

## 2026-09-18：現状確認（M6の作業ツリーを含む）

- Gitの最新コミットはM4（febffc3）。M1〜M4はコミット済み。M5に加え、M6のテンプレート・イベント・環境診断・ツール指定・infoの実装と公開API接続、`tests/test_workflow.py`、`examples/complete_workflow.py`が未コミットで存在する。「M6未着手」の記載は作業ツリーと一致しないため更新した。
- 最初のテスト実行はサンドボックス内の一時フォルダーへのアクセス制限で失敗（58件収集、112エラー）。承認後、制限外で子プロセス環境を正規化し、`CPPBUILD_TEST_VS2022=1`と既存の固定版GoogleTest ZIPを指定して再実行した。
- `python -m unittest discover -s tests -v`：全58件成功、skipなし、93.056秒。通常47件と実VS2022試験11件。M6追加分は通常11件と実試験1件で、テンプレート復元後の実ビルド・実行、環境診断、infoの状態、イベントとファイル素材の操作を含む。
- `git diff --check`成功。今回、製品コードは変更せず、現状確認と進捗記録の更新を行った。
- M6の具体的な結果型・テンプレート参照・イベント規則等は現行詳細資料への反映が残る。`examples/complete_workflow.py`自体は今回実行していない。AC全項目の最終確認・自己レビュー・コミットを済ませたとは扱わない。
- 次の作業：M6の実装選択を資料に反映し、統合利用例を実行してAC・自己レビューを確認する。その後M5・M6の未コミット変更を整理する。GoogleTestオンライン取得は従来のユーザー指示に従い保留。VS IDEのGUI表示も未検証。

## 2026-09-18：GoogleTestオンライン導入・M5/M6最終確認

- ユーザーの実装継続依頼に基づき、オンライン取得の確認を再開した。サンドボックス内では`https_proxy=http://127.0.0.1:9`への接続が拒否され、GitHubからのダウンロードが失敗。制限外実行の承認を得て再実行した。
- `python -m examples.complete_workflow .test-work/online-workflow-approved-20260918`が終了コード0で成功。ローカルZIPを指定せず、元と復元先それぞれのProject所有ツリーでGoogleTest 1.14.0を取得・ハッシュ検証・ビルドした。共有素材からのヘッダー作成、イベントによるテスト追加と更新集約、環境診断、全体build/test、Solutionテンプレート作成・復元後のtestまで確認。出力は`Answer.Value passed`、LibraryとTestsのgeneration/build状態はcurrent。
- M6のAC確認：設定・ソース・素材の復元と生成物除外、素材の作成・登録・展開・解除、個別通知・再帰抑止・失敗・更新集約、実ツール条件での診断とinfoのunknown/stale/current/failed/cleanedは`test_workflow.py`で確認。公開APIの一連の利用は上記統合利用例で確認した。
- 自己レビュー：テンプレートの重複／入れ子／コピー失敗、外部参照の非内包、イベントの例外・再入・非同期runの通知時点、infoが外部走査をしない制約、ツール設定の非保存と子プロセス環境を確認。通知の深さ制限で実際には再送しないため、誤解を招くエラー文言`deferred`を`skipped`へ修正した。
- API・設定資料・設計メモへM6の具体的な結果型と規則を反映し、`deliverables/USAGE.md`へオンライン／オフライン導入と利用手順を追加した。
- 修正後の最終テスト：承認済みの制限外実行で、環境正規化・`CPPBUILD_TEST_VS2022=1`・固定ローカルZIP指定により全58件成功（通常47件、実VS2022試験11件、skipなし、90.442秒）。オンラインの成功は上記利用例で別途検証した。
- M5とM6の変更はAPI接続部が共通のため、一つのコミットにまとめる。本記録を含むコミットでM5・M6を完了とする。`.git`の書き込みは通常サンドボックスで許可されないため、ステージ・コミットは制限外実行を申請する。
- 次の作業：予定したM1〜M6の追加実装は残っていない。VS IDEのGUI表示は引き続き未検証。ネットワーク取得の保留は解消した。実装の制約を変更する拡張は新しい依頼として扱う。
