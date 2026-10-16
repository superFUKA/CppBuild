# CMakeラッパーとしての責務・複雑さのレビュー

## 結論

**簡素化できる独自実装はある。最優先はCMake内部ファイルの解析を公式の取得方法へ寄せること。最も生成器への依存が強いのはcleanで、最も多くの独自ルールを必要とするのは形式の同時利用・表示専用Project・Git依存管理である。**

現在のCppBuildは、CMake呼び出しに加え、Projectの管理・依存の解決・CMake定義の生成を担っている。CMakeラッパーとしての簡潔さを優先するなら、コードの分割だけでなく、これらの追加機能の保証範囲を決める必要がある。

一方、コンパイル・リンク・差分ビルド・実際のビルド順はCMakeと生成先ツールへ委譲している。標準のtargetコマンド、File API、CTest、GoogleTestモジュールを使う中心部分は妥当である。

## 対象と方法

- 対象：`5d3ecfe` と現在の作業ツリー。Git取得機能を含む。既存の未コミット変更である `solution_engine.py` と `test_linked_outputs.py` も確認し、変更していない。
- 実装とCMake公式資料を静的に照合。基本は最小対応版の3.24資料を使い、一部は現行公式資料で補完した。
- 今回は製品コードの変更、実ビルド、性能測定、代替方式の試作を行っていない。以下の「置き換え候補」は実装済み・同等性検証済みを意味しない。
- 既存合意を撤回せず、要件を変えずに改善できる箇所と、仕様判断が必要な箇所を区別する。

## 評価一覧

| 対象 | 現在の位置づけ | 評価・優先度 |
| --- | --- | --- |
| コンパイラ情報の内部ファイル解析 | CMakeの生成物の書式をPythonが解釈 | 高：公式File APIへの置き換え候補 |
| コンパイラ自動探索の模倣 | CMakeの判断を事前に推測してツリー識別に利用 | 中：要求と検出結果を分け、推測を減らす |
| Project単位clean | CMake共通CLIより細かい保証を独自実装 | 高：対応範囲を限定して隔離。全面簡素化には要件判断が必要 |
| 形式の同時利用・別名の切り替え | 標準ターゲット上の独自ポリシー | 中〜高：複雑さの大きな原因。単なるBUILD_SHARED_LIBSでは置き換わらない |
| 表示専用Projectとその依存 | 標準の除外プロパティ上の独自ポリシー | 中：表示要件が生む複雑さ。通常のリンクと責務を分ける |
| Pythonと生成CMakeのGit取得 | 取得手順の二重実装、独自の依存管理 | 中〜高：取得手順の共通化またはFetchContent適用を検討 |
| ImportedLibraryの未提供構成 | 不在パスによって失敗をビルド時まで遅延 | 中：独自の失敗タイミングを本当に維持するか判断 |
| ビルド状態の独自判定 | APIが把握する観測情報 | 中：表示用途に限定し、差分ビルドの判断を増やさない |
| ソース走査・明示リスト生成 | 管理APIからCMakeへの変換 | 維持：GLOBへの一律置き換えは勧めない |
| GUID・管理JSON・移動・テンプレート | CMake外の管理機能 | 役割上は説明可能。生成・実行経路から分離する |

## 1. 内部のコンパイラ情報ファイルを解析している

根拠：[generators.py](../cppbuild/generators.py) の `compiler`（281行付近）。

現在は `CMakeFiles/*/CMakeCXXCompiler.cmake` を探し、`set(...)` の書式を正規表現で読んでいる。さらに `CMakeSystem.cmake` を読む。CMake内部の保存方法が取得契約になっている点が、ラッパーとして不要な結合である。

コンパイラのパス・ID・バージョンは、既に採用しているFile APIの `toolchains-v1` で取得できる。現状は `codemodel-v2` だけを要求しているため、問い合わせを拡張する方向が自然である。[公式File API：toolchains](https://cmake.org/cmake/help/v3.24/manual/cmake-file-api.7.html#object-kind-toolchains)

**推奨：** この3項目はFile APIへ移す。アーキテクチャはFile APIだけで現在の全判定が置き換わるとは限らないため、生成器のplatform、明示されたtoolchain、必要なら生成CMakeから明示的に出力する検出値を利用する。正規表現を増やして内部ファイルへの依存を深めない。

併せて `codemodel` / `target_data` の繰り返し読み込みを操作内で共用できる。ただし、性能問題の実測はしていない。

### コンパイラの自動探索も縮小候補

`generators._resolve` は `CXX` または `c++ / g++ / clang++` をPythonで探索し、ツリーの識別に使う。CMake側の探索と一致し続ける保証を自分で持つ必要がある。例えばCMakeの `CXX` は必須引数を含む値を許すが、現在の処理は値全体を `shutil.which` に渡す。[公式CXX環境変数](https://cmake.org/cmake/help/latest/envvar/CXX.html)

明示指定の検証は残し、自動選択はCMakeの構成結果を正とするのがよい。環境変更時のツリー分離・再利用方針も関連するため、探索だけを削除する変更では済まない。

Ninja＋MSVC用の `vcvarsall` 呼び出しは別に扱う。CMakeはコマンドライン生成器ではコンパイラ環境が準備済みであることを期待するため、通常のシェルから動かすという利便性には理由がある。VS生成器の検出処理まで同じ仕組みに拡張する必要はない。[公式cmake CLI](https://cmake.org/cmake/help/v3.24/manual/cmake.1.html#introduction-to-cmake-buildsystems)

## 2. cleanはCMake共通の抽象化を越えている

根拠：[engine.py](../cppbuild/engine.py) の `clean_targets`、`_msbuild_clean`、`_remove_outputs`（340〜411行付近）。

現在の実装は次を直接扱う。

- VS：`.vcxproj` とMSBuildの配置を推測し、MSBuildのCleanを呼ぶ。
- Ninja全体：`build-<構成>.ninja` を指定して `ninja -t clean` を呼ぶ。
- Ninja個別：File APIの成果物を削除し、`CMakeFiles/<target>.dir/<構成>` を推測して削除する。

特に最後のオブジェクトディレクトリ推測は、CMakeの内部配置への依存である。File APIの `artifacts` は依存側が使う成果物の一覧であり、全中間生成物・カスタムコマンド出力のclean一覧ではない。[公式File API：target](https://cmake.org/cmake/help/v3.24/manual/cmake-file-api.7.html#codemodel-version-2-target-object)

CMake公式CLIは `--target clean` と `--clean-first` を提供する。一方、確認した共通CLIには「指定ターゲットの生成物だけを、再構成を絶対に起こさず削除する」という操作はない。[公式ビルドCLI](https://cmake.org/cmake/help/v3.24/manual/cmake.1.html#build-a-project)

**評価：** 現在の「clean前に再生成しない」「Project.cleanは対象Projectだけ」という合意が独自処理を必要にしている。この2条件を保ったまま、`cmake --build --target clean` に一律置換する提案はできない。

**推奨：** 条件を維持する間は生成器別の処理として隔離し、対応版・扱える生成物を限定する。今後の簡素化方針として、全体cleanを標準動作に寄せ、個別cleanの保証を縮小する選択肢があるが、これは仕様変更の判断になる。

VSの実行ファイル探索は、CMakeが提供する `CMAKE_VS_MSBUILD_COMMAND` を構成時に記録する方法が候補。通常変数なので、キャッシュに常にあるとは仮定しない。また `CMAKE_GENERATOR_INSTANCE` はパス以外に `version=` 等を含む形式を許すため、値全体をインストールディレクトリとみなす現在の処理は、公式に許される指定を狭く解釈している。[MSBuildの公式変数](https://cmake.org/cmake/help/latest/variable/CMAKE_VS_MSBUILD_COMMAND.html)、[VSインスタンスの指定形式](https://cmake.org/cmake/help/v3.24/variable/CMAKE_GENERATOR_INSTANCE.html)

## 3. 形式選択と表示の独自ルールが生成処理を複雑にしている

根拠：[cmake_files.py](../cppbuild/cmake_files.py) の `_requested_types`・`_library`、[workspace.py](../cppbuild/workspace.py) の `_requests`、[core.py](../cppbuild/core.py) の `_resolved_build_settings`。

形式を選んで `add_library` を出すだけでなく、現在は次を同時に実現する。

- 同じProjectを静的・共有の両方で作り、利用側ごとに形式を要求する。
- 提供側の上書きで、保存されたリンク形式が指す実体を変更する。
- インターフェース形式も同じProjectの選択肢に含める。
- 表示専用の形式と通常ビルドする形式を区別する。
- 同時利用に伴って別名・出力名・出力先を調整する。

このためPythonと生成CMakeの両方が、選択形式と必要形式を判断している。CppBuildなしの利用時にも切り替える要件があるので、CMake側の判断自体は必要である。

STATIC・SHARED・INTERFACE・ALIASはCMakeの標準機能であり、それらを組み合わせることはサポート範囲内。`BUILD_SHARED_LIBS`は型省略時のSTATIC/SHARED選択なので、現在の同時利用・要求ごとの切り替えまで代行するものではない。[公式add_library](https://cmake.org/cmake/help/v3.24/command/add_library.html)

**推奨：** 現機能を維持するなら、選択規則を一か所の仕様表にし、Pythonと生成CMakeの対応を検査する。生成側の共通規則を小さな同梱 `.cmake` にまとめる案もあるが、単独利用用の配布物・版管理を増やすコストと比較する。関数を移すだけでは独自ルールは減らない。

さらに薄くするなら「1ツリーで1Projectは1形式」「静的・共有の同時利用は別ターゲットとして明示」という方針が候補。ただし既存の同時利用要件に関わるため、現在の仕様のまま採用できる修正ではない。

### IDE表示

フォルダー配置は標準の `FOLDER` / `CMAKE_FOLDER` で表せており、妥当。一方、リンク先の未使用Projectもビルド可能なターゲットとして表示する要件は、依存解決・ソース検証・形式要求まで増やす。[公式FOLDER](https://cmake.org/cmake/help/v3.24/prop_tgt/FOLDER.html)

`EXCLUDE_FROM_ALL`と`EXCLUDE_FROM_DEFAULT_BUILD`を使う処理自体は標準の使い方。表示だけなら依存が不要になるわけではなく、表示したものをIDEからビルドできる保証にコストがある。軽量な一覧表示へ変える案は、IDE上の使い方の変更になる。[公式EXCLUDE_FROM_ALL](https://cmake.org/cmake/help/latest/prop_tgt/EXCLUDE_FROM_ALL.html)

## 4. Git取得は依存管理機能へ広がり、手順が二重になっている

根拠：[git_sources.py](../cppbuild/git_sources.py) の `walk`・`clone`・`fetch`、[cmake_files.py](../cppbuild/cmake_files.py) の `GIT_SOURCE_FUNCTION`（31行付近）。

Pythonと生成CMakeのそれぞれに、clone・checkout・一時ディレクトリ・移動・後始末・既存版の確認がある。加えてPythonはURLの同一性、間接依存の統合、最上位の記録の優先、コミットの食い違いを管理する。これは追加の依存管理機能であり、単純なビルドラッパーより広い責務である。

CMakeのFetchContentにはGitの取得、コミット指定、階層的な依存の宣言と取り込みがある。取得部分を標準機能へ寄せる余地はある。[公式FetchContent](https://cmake.org/cmake/help/v3.24/module/FetchContent.html)

ただし現在は「ソース側の指定場所へ置く」「既存の作業版は変更しない」「記録と違えば警告」「GUID解決のため生成前に管理JSONを読む」という固有条件がある。既存ソースを指定するFetchContentの仕組みはあるが、そのまま置換してすべて同じ挙動になるとは限らない。[既存ソースの指定](https://cmake.org/cmake/help/latest/module/FetchContent.html#variable:FETCHCONTENT_SOURCE_DIR_%3CuppercaseName%3E)

**推奨：** Git取得・依存記録の管理を明示的な一機能として分離する。まず「どのコミットを採用するか」と「どう取得するか」を分け、後者の二重保守を減らす。FetchContentへの移行は、既存作業版の保護・固定配置・オフライン時の挙動を小さく試作して判断する。Python側にも生成前の取得が必要なので、生成CMakeだけ書き換えて完了とはしない。

## 5. 未提供構成のImportedLibraryに架空のパスを設定している

根拠：[cmake_files.py](../cppbuild/cmake_files.py) の `_external_setup`（310行付近）。

4構成のうちバイナリーがない構成には、意図的に存在しない `cppbuild-missing/...` を `IMPORTED_LOCATION_<CONFIG>` に設定する。これは「他の構成を生成できるが、未提供構成のビルドは失敗する」という独自の失敗タイミングを作っている。

CMakeの `IMPORTED_CONFIGURATIONS`・`IMPORTED_LOCATION_<CONFIG>`・`MAP_IMPORTED_CONFIG_<CONFIG>` は標準の対応手段。マッピングを明示し、合う構成がなければ見つからない扱いにできるが、生成時の失敗に変わる可能性がある。[公式MAP_IMPORTED_CONFIG](https://cmake.org/cmake/help/latest/prop_tgt/MAP_IMPORTED_CONFIG_CONFIG.html)

**推奨：** 自動的にReleaseへ代替する変更はしない。提供構成を明示し、不適合を生成時に返してよいなら標準のマッピングへ寄せる。未提供構成を含めて生成成功させる要件を残すなら、現在の回避策は必要性を説明して局所化する。

## 6. 状態・依存グラフは管理情報の範囲にとどめる

根拠：[information.py](../cppbuild/information.py) の `signature`、[graph.py](../cppbuild/graph.py) の `resolve`。

GUIDからProjectを探し、CMakeに渡す対象を決めるグラフは必要。`info()`用の直近の操作記録も有用。現状の `current/stale` は設定とAPIが把握した変更の観測値であり、コンパイラの依存解析やネイティブビルドツールの差分判定そのものではない。

**推奨：** 将来、タイムスタンプ・ヘッダー依存・コンパイルコマンドの独自比較を足して「ビルドを省略してよい」と判断する方向へ広げない。実際のビルド要否はCMakeが生成したビルドシステムへ任せる。現在もbuild呼び出し自体をこの状態だけで省略していない点は維持する。

なお、全依存サイクルを拒否する現在の検査はCMakeより厳しい。CMakeは相互依存するSTATICライブラリを扱える。今すぐ対応を増やす提案ではなく、「CMakeの制限」ではなく「CppBuildが扱う範囲」として説明するのが適切である。[公式STATICライブラリの循環依存](https://cmake.org/cmake/help/v3.24/command/target_link_libraries.html#cyclic-dependencies-of-static-libraries)

## 過剰とは判断しなかった部分

- **明示的なソース一覧の生成：** Pythonで走査してCMakeへファイル一覧を渡す方式は、管理APIの役割に合う。CMake公式もソース一覧収集へのGLOB利用を推奨していないため、「標準機能だからGLOBへ置換」は勧めない。素のCMake利用時のファイル追加には一覧更新が必要になる制約は残る。[公式file(GLOB)](https://cmake.org/cmake/help/v3.24/command/file.html#filesystem)
- **PCH・インクルード・定義・リンク設定：** `target_precompile_headers`、`target_include_directories`、`target_compile_definitions`、`target_link_libraries`への変換は本来のラッピング範囲。
- **GoogleTest・CTest：** `FetchContent`と`gtest_discover_tests`を使い、Pythonは実行と結果整形を担う。テスト列挙・実行基盤を別に作っていない。[公式GoogleTestモジュール](https://cmake.org/cmake/help/v3.24/module/GoogleTest.html)
- **生成文書の所有確認・ロック・失敗時の復元：** CppBuildが複数のソースディレクトリへ生成文書を書く以上、CMakeが代行する責務ではない。生成物やユーザーのファイルを守るために残す。
- **管理JSON・GUID・移動・テンプレート：** CMakeにはない管理機能だが、現在のProject管理APIの要件に対応する。ビルド処理が必要以上に管理機能を呼び出さない境界を作る方が先。
- **CMakeの機能をすべて公開しないこと：** 対応生成器、C++規格、構成、ライブラリ種別を限定することはラッパーの選択として成立する。CMakeの対応範囲とCppBuildの対応範囲を区別すればよい。

## 推奨する進め方

1. **公開仕様を保った改善：** コンパイラ情報をFile APIへ移す。MSBuildの所在はCMakeが提供する値を記録する方向で検証。生成器固有のcleanを隔離する。
2. **重複の削減：** Git取得手順、形式選択規則、全体／個別の操作対象・結果記録を整理する。新たな継承階層や汎用フレームワークは不要。
3. **仕様判断が必要な簡素化：** Project単位cleanの厳密保証、同一Projectの複数形式同時利用、表示専用Projectのビルド可能性、依存cloneの配置・保護、ImportedLibraryの失敗タイミングを検討する。

設計上の判断基準は、**CMakeが決めるコンパイル・リンク・生成器の挙動を再実装せず、CppBuildは入力の管理と変換、呼び出し、結果の取得に集中すること**。現在の追加要件をすべて維持する場合、独自処理を完全に取り除くことより、その範囲を限定する方が現実的である。
