# 実装・検証記録

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
