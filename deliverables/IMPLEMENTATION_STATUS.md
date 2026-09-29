# 実装・検証記録

## 2026-09-19：公開APIの利用検証を公開用に整理

- `usage_tests/`にC++ Solutionの作成からビルド・実行・CTestまでを行う6シナリオと再現手順を追加。既存のローカル集計とRESULTS.mdを照合し、全6シナリオ成功、操作・確認84件を確認した。これは既存unittestの58件とは別の利用検証である。
- README・開発手順から検証手順へリンクし、ソース配布対象にもランナーと資料を追加。生成されたC++プロジェクト・バイナリー・詳細ログはGit除外対象とし、公開資料のローカル生成物へのリンクはパス表記へ変更した。
- 今回は製品コードを変更していない。過去の実ビルド結果の照合とランナーのCLI確認を行い、C++実ビルドは再実行しない。コミット・pushの成否はGit操作結果で別途確認する。

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

## 2026-09-28：Project移動の調査

- 実装を読み、Project登録・依存・生成物・非保存設定の移動影響を調査。必要手順とAPI上の制約をDESIGN_NOTES.mdの同日項目に記録した。
- 一時的な管理データによる検証で、登録パス変更の公開save拒否、既存設定の再add拒否、JSON修正後のreload・内部依存解決、旧オブジェクト無効化、非保存設定初期化、移動した所有マーカーによるupdate拒否を確認した。検証データはrelocation-check-l3g0bdd1/に残る（片付けの再帰削除コマンドが実行ポリシーに拒否された）。
- 製品コードの変更、実プロジェクトの移動、移動後のCMake生成・VS実ビルドは行っていない。次に移動機能を実装する場合は、実移動／参照更新の責務と失敗復旧を具体化し、依存利用側を含む実ビルドを検証する。

## 2026-09-28：Project移動APIの実装・検証

- 調査後の追加依頼を受け、Solution.move_project(name, destination, *, auto_update=True)を実装。ユーザーの指示に合わせ、既存メソッドと同じ引数形式・FileOperationReportを使用した。戻り値のupdateは全体更新のOperationReportにも対応する。
- 名前・主Project・依存ID・Project/Settingsオブジェクト・非保存設定を保持して実フォルダーと登録を移動。内部／外部参照の型付きパスを補正し、移動対象の全種類／全アーキテクチャのgenerated/buildを全体.cppbuild/relocationsへ退避する。自動更新は全体.slnと利用側も再生成する。
- 設定競合と無効な配置を変更前に拒否し、rename・保存の失敗では設定・実フォルダー・キャッシュを復元する。移動完了後のCMake更新失敗は移動を維持し、既存のpending_update/update_error形式で返す。同時操作・強制終了時の制約は設計整理メモとAPI資料へ記載した。
- 追加試験10件：通常9件（オブジェクト・設定・依存の保持、型付きパスの補正、全キャッシュ退避、配置拒否、設定保存／rename失敗の復元、競合・操作中・コールバック内拒否、更新成功／失敗）、実VS2022試験1件（依存ライブラリ→アプリの移動、Debug/Releaseの全体／個別build/run、clean/rebuild、再open後の実行）。
- 最終確認：CPPBUILD_TEST_VS2022=1とSHA256確認済みの既存GoogleTest 1.14.0 ZIPを指定し、python -m unittest discover -s tests -vを実行。全68件成功（通常56件、実VS2022試験12件、skipなし）、109.958秒。最新コードでビルドして実行する試験を含む。成功時の各ビルド出力はテストが抑制するため、警告一覧の別途採取は行っていない。git diff --checkも成功。
- API・要望・設定・生成物・設計メモ・利用手順・引き継ぎを更新。実装の自己レビューで、変更しないProjectの設定指紋を保持する処理と、途中まで作成された移動先親ディレクトリの復元を確認・修正した。
- 次の作業：今回の移動APIに残る実装・試験作業はなし。別Solutionへの移管、移動済みフォルダーの登録修復、名前変更、強制終了後の自動復旧は対象外。VS IDEのGUI確認は未実施。変更は未コミット。
## 2026-09-29：ソリューションフォルダーの実装・検証

- 合意した `SolutionFolderSettings(projects, linked_projects, project_folders)` と保存項目 `SolutionSettingsData.solution_folders` を実装。get/save/updateで操作し、旧ファイル・既定Noneは従来表示を維持する。登録解除時の配置削除、移動時の保持、テンプレート復元に対応。
- 管理Projectを任意ルート配下の相対階層へ配置。外部Solutionは間接依存も含めて集め、Solution名ごとに全所属Projectを表示する。同名の別Solutionはパス由来の識別子で区別。外部Projectを自分の所属一覧へ追加しない。
- 表示のみのProjectをVS既定ビルドとCMake ALL_BUILDの双方から除外。全体ビルドのMSBuild対象名に階層と特殊文字の変換を反映。全体テストで見つかった複数targetの区切り文字を変換する不具合を修正し、修正後に全件再実行した。
- 追加8件（通常7件・実VS2022試験1件）。保存互換性、コピー分離、不正指定、依存範囲と全所属表示、間接依存、同名Solution、複数種類、Project移動・解除、テンプレート復元、表示解除を確認。実試験は日本語・空白・括弧・ピリオドを含む階層、複数ProjectのDebug/Releaseビルド・実行、NestedProjectsの親子GUID、Build.0除外を検査。意図的にコンパイル不能な未参照Projectを表示して、APIビルドとCMakeの既定全体ビルドが成功することを確認した。
- 最終検証：`CPPBUILD_TEST_VS2022=1`、SHA256確認済みの既存GoogleTest 1.14.0 ZIP、作業領域内TEMP/TMP、正規化した子プロセス環境で `python -m unittest discover -s tests -v`。全76件成功（通常63件・実VS2022試験13件・skipなし）、124.881秒。ログは `.test-work/folders-tests-final.log`。検証中に一時ディレクトリ使用に関するMSB8029を確認。成功した全ビルドの警告一覧は別途採取していない。
- 自己レビュー：所属・表示・依存ビルドの境界、全体cleanの所有範囲、設定の非干渉、特殊文字、旧管理ファイルの読込、テンプレート保存、複数targetの扱いを確認。API・要望・設定・ビルド設計・利用手順を更新。差分の空白検査成功。
- 制約：表示用Projectも構成可能であることが必要。GoogleTest取得などの構成処理は発生し得る。全体操作の構成整合条件は表示用Projectにも適用。空の仮想フォルダーは生成しない。VS IDEのGUI操作は未検証（生成.slnと実MSBuildで確認）。
- 現在のGit履歴では移動APIは `d287be3` でコミット済み。今回のフォルダー機能は未コミット。この実行環境では `.git` が読み取り専用。今回の機能に残る実装・自動試験作業はなし。次の作業は書き込み可能なGit環境でのコミットと、必要に応じたVS IDEのGUI確認。
- コミット前再確認（2026-09-29）：`CPPBUILD_TEST_VS2022=1` とSHA256一致を再確認した既存GoogleTest 1.14.0 ZIPを指定し、正規化した子プロセス環境で全テストを再実行。全76件成功（通常63件・実VS2022試験13件・skipなし）、119.735秒。今回の階層表示と既存機能の実試験が成功。過去の `feat:` コミット（M3b・M4・M5〜M6・移動API）と照合し、実装・追加テスト・関連資料の18ファイルを一つの機能コミットにまとめる粒度が整合することを確認。既存の `relocation-check-l3g0bdd1/` はアクセス拒否により内部未確認のため、コミット対象に含めない。コミットは未実施。次の作業は確認済み18ファイルを明示指定してコミットすること。VS IDEのGUI確認は引き続き未実施。

## 2026-09-30：GUIDと名前付き外部リンクの自動管理

- `project.settings.link_solution(config_directory, link_type, *, name=None)` を実装。2引数の呼び出しを維持し、名前省略時は対象ProjectのGUIDを使用する。呼び出し時に所属Solutionのreferencesへ自動登録。同名・同じGUID・同所在は共用し、異なる対象・所在はエラー。別名の同じ対象は登録できる。
- Project作成時に永続GUIDを発行し、通常saveでの変更を拒否。内部リンクにもGUIDを保存する。外部の対象は登録時の主Projectに固定し、主Project変更では切り替えない。GUIDと種類で依存を集約するため、別名や複数利用元でもビルド・全体.slnに重複しない。
- `unlink`、Project設定saveによる依存削除、Project登録解除で最後の利用がなくなった参照を自動削除。別名は独立して整理する。対象のファイル・成果物は削除しない。事前登録・手動削除・所在更新の専用APIは追加せず、一覧は `solution.settings.get().references` から取得する。
- Project移動でGUIDを保持。テンプレート作成・復元では所属ProjectのGUIDを新規発行し、内部依存を付け替え、外部参照を保持する。旧設定はopen/reload時にGUIDと参照一覧へ自動移行し、解除用dependency_idを保持する。
- 保存時はSolutionと全所属Projectのロック・指紋を検査し、通常の書き込み失敗では複数文書を復元する。旧形式の循環依存はGUIDの付与と依存の移行を分けて処理し、依存解決時に循環を診断する。移行済みの再読み込みでは書き込まない。
- 追加16件（通常15件・実VS2022試験1件）。参照共有・別名・衝突・最終解除・Project解除・直接save・GUID不変性・移動・主Project変更・テンプレート・旧形式移行・循環・失敗復元・古い利用元一覧の競合・個別更新の独立性を確認。既存の管理テストは新規作成時にGUIDを発行する仕様に合わせて更新。
- 実試験では二つのアプリと別名参照が同じ外部ライブラリを共用し、Debug/Releaseの全体ビルド・実行、全体.slnの対象一件、外部Project移動後の実行、最後の利用元削除後の表示解除を確認。
- 最終検証：`CPPBUILD_TEST_VS2022=1`、SHA256一致を再確認した既存GoogleTest 1.14.0 ZIP、正規化した子プロセス環境で `python -m unittest discover -s tests -v`。全92件成功（通常78件・実VS2022試験14件・skipなし）、130.410秒。`git diff --check` 成功。成功したビルドの警告一覧は別途採取していない。
- 自己レビューで個別依存解決が無関係な兄弟Projectまで再読込する変更を修正し、回帰試験を追加して全実試験を再実行した。API・利用手順・保存形式・生成方式・要件・設計選択・進捗を資料へ反映。
- 制約：旧形式の初回移行には書き込み権限と旧外部リンク先が必要。利用側の移行失敗時も外部側で完了したGUID付与は保持する。強制終了後の自動復旧は未対応。外部Solution全体の移動先探索は行わず、全利用元でunlink後に新しいパスへ再リンクする。VS IDEのGUI確認は未実施。
- フォルダー機能は `5e4c015` でコミット・push済み。今回の名前付きリンク変更は未コミット。残る実装・自動試験作業はなし。次の作業は差分確認後のコミット。既存のアクセス不能な `relocation-check-l3g0bdd1/` は変更していない。

## 2026-09-30：設定ファイル基準の相対パス保存

- 管理JSONをschema_version=2に変更し、型付きパスをそれぞれの設定ファイルの親ディレクトリ基準・`/` 区切りで保存する。所属Project・共有素材・ソース・PCH・種類別ファイル・include・外部Solution・CMakeソース／パッケージ・ImportedLibraryのDLL/LIB/includeを対象にした。APIの引数と相対パスの入力基準は維持する。
- 絶対パス入力は保存時に相対化。読み込み時はAPIのSolution／Projectルート基準へ戻す。別ドライブ／別共有への参照など、相対化できない場合はSettingsErrorとし、絶対パスの保存へ戻さない。
- schema_version=1は従来の基準で読み込み、open/reload時に移行する。GUID・リンク名・dependency_idを保持し、旧種類ファイルから新しい内容のファイルへ参照を切り替える。移行済みの再読み込みは保存しない。途中失敗では公開済み文書を既存の仕組みで復元する。
- Project移動のSolution文書出力を新形式へ対応。テンプレートは一時ディレクトリから最終配置へ公開する直前にパスを補正し、コピー対象の内部参照と外部への参照をそれぞれ維持する。
- 追加9件（通常8件・実VS2022試験1件）。各JSON基準でのパス解決、異なる深さ・日本語・空白を含む配置への移設、元配置を使えなくした状態と異なる作業ディレクトリ、移設後の参照共用、テンプレート、旧絶対パスの移行・失敗復元、不正な絶対パス／別ドライブの拒否、サブフォルダー内の種類別設定、不正な依存種別の読み込みを確認。既存テストの旧形式データをschema_version=1で明示し、相対化後の設定値に合わせて移動テストを更新した。
- 最初の全実試験で、外部CMakeソースの子targetが全体ReleaseビルドでDebugへ戻る既存問題を検出。VS2022のMicrosoft.Common.CurrentVersion.targetsで既定動作を確認し、APIの全体MSBuildにShouldUnsetParentConfigurationAndPlatform=falseを追加。対象7件が成功した後、全実試験を再実行した。
- コミット前レビューで2件の問題を再現して修正。種類別JSONをtypes配下のサブフォルダーへ置く場合は、読み込みに所有Projectのルートを明示し、旧形式の絶対パスと新形式の相対パスを正しく解決する。不正な依存種別が配列・オブジェクトの場合もSettingsErrorを返し、設定ファイルと読み込み済み状態を変更しない。追加した回帰試験2件で修正前の失敗と修正後の成功を確認した。
- 最終検証：CPPBUILD_TEST_VS2022=1、SHA256一致を確認済みの既存GoogleTest 1.14.0 ZIP、正規化した子プロセス環境で `python -m unittest discover -s tests -v`。コミット前レビューの修正後に全101件成功（通常86件・実VS2022試験15件・skipなし）、149.011秒。移設先で外部SolutionとCMakeソースを使い、Debug/Releaseの全体・個別ビルドと実行が成功。`git diff --check` 成功。実行環境は同一Windows上で移設先と作業ディレクトリを変更したもので、別PCでの試験は未実施。
- 自己レビュー：JSONとAPIのパス基準、種類別ファイルの参照・指紋、移行の復元と再読み込み、テンプレートの最終配置、Project移動、構成の引き継ぎを確認。API・保存仕様・利用手順・要件・設計・進捗を更新した。
- 制約：移設前に旧設定を読み込んで移行し、利用側と依存先の相対配置を維持する。すでに移設された旧絶対パスから新しい所在を推測しない。既存CMakeキャッシュ・生成物・所有マーカーは移設先へ持ち込まず再生成する。定義・任意引数・外部CMakeListsやソース本文のパスは書き換えない。移行後の未参照の旧種類ファイルは自動削除しない。成功した全ビルドの警告一覧採取とVS IDEのGUI確認は未実施。
- 名前付きリンク機能は `17428b9` でコミット・push済み。過去の機能コミットの粒度と照合し、今回の相対パス変更・レビュー修正・テスト・関連資料を一つにまとめ、本記録を含むコミットで完了とする。残る実装・自動試験作業はなし。既存のアクセス不能な `relocation-check-l3g0bdd1/` は変更せず、コミット対象から除外する。
