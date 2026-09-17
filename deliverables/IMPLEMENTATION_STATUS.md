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

M3以降は未実施。全体統合・共有依存clean・GoogleTest・外部依存・テンプレート・イベントを実装済みと扱わない。
