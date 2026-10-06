# 設計整理メモ

## 2026-10-06相談：Windowsのパス長（失敗時の案内のみ実装）

ユーザーの指示（「やったほうがよさそうな修正だけ」）により、下の検討結果のうち失敗時の案内だけを実装した。短い中間ファイル名と既定の置き場所の短縮は見送り。設定ファイルの書き込みで止まるのはルートが約155文字からで、その前に構成が必ず失敗して案内が出るため、対応しない。

- 中間ファイルのパスは `.cppbuild\output\intermediate\<10桁>\<Projectの相対パス>\<Solution名>_<Project名>.dir\<構成>\<ソース名>.obj`（VS生成器。Ninjaは `CMakeFiles\` が加わる）。名前が2回入るのは、ターゲット名が `<Solution名>_<Project名>` で、CMakeが `<ターゲット名>.dir` を作るため。
- 案（実測あり）：CMake 4.2の `CMAKE_INTERMEDIATE_DIR_STRATEGY=SHORT`（VS・Ninja・Makefileが対応）。VS2022で `LongSolutionName_LongProjectName.dir\Debug\main_with_long_name.obj` が `.o\caa81679\Debug\949f3994.obj` になることを確認した。4.2より前のCMakeでは無視され、従来どおりになる。キャッシュ変数のため、生成ファイルにキャッシュの既定値として書けば素のcmakeでも効く見込み（未確認。今回の実測は `-D` で指定）。欠点はオブジェクト名がハッシュになること。
- 案：分かりやすいエラー。ビルド前に「ビルドツリー＋Projectの相対パス＋ターゲット.dir＋構成＋最長のソース名.obj」を見積もり、260文字を超えれば警告またはエラーにする。cl.exeが長いパスをどこまで扱えるかは未確認。
- 案：既定の置き場所 `.cppbuild\output\intermediate\` を短くする。`intermediate_directory` で既に変えられ、既定を変えると既存のビルドツリーを作り直すことになる。
- 実測（2026-10-06、LongPathsEnabled=0、CMake 4.2.3、Solution名 `MySolution`・Project名 `MyProject`・`src/MyProject`、既定の中間領域）。Solutionのルートの長さを変えてProjectをbuildした。
  - VS2022：ルート93文字で成功、96文字で失敗。失敗はCMakeが構成時に行うコンパイラの確認（`TryCompile-*`・`VCTargetsPath` のtlog）で、エラーは `MSB4018 "GetOutOfDateItems" タスクが予期せずに失敗` などで原因が分かりにくい。成功時のProjectのobjは200文字で、まだ余裕がある。**VSでは名前の重複より先にCMake・MSBuildの内部パスが限界に達し、その位置はビルドツリーの長さだけで決まる。**
  - Ninja：ルート115文字で成功（Projectのobjは241文字、CMakeの250文字超過の警告あり）、130文字で失敗（コンパイラの確認で `C1083`）。ソースの階層が深いとProjectのobjが先に限界に達し得る。
  - ルート約155文字では、CppBuild自身の設定ファイルの書き込み（`add_project`）がPythonの例外で止まる。
- 検討結果（提案）：
  - 名前の重複の短縮（SHORT）は**今は見送り**。VSでは失敗する位置が変わらない。Ninjaで効くのはソースが深い場合だけで、objの名前がハッシュになる欠点がある。
  - 既定の置き場所の短縮は効果が約24文字で、限界の性質は変わらない。既存のツリーの作り直しも伴うため**優先度は低い**。
  - 原因が分かるエラーは**対応する価値がある**。今のエラーは長さが原因だと読み取れない。ただし限界は生成器・CMakeの版・内部の名前で変わるため、事前に止めると誤検出のおそれがある。構成・ビルドが失敗し、かつビルドツリーが目安（VSで約130文字、Ninjaで約155文字）を超えるときに、`intermediate_directory` を短い場所へ指定する案内を結果へ添える方式を推す。

## 2026-10-03実装：gitのURLによるリンクと自動取得

依頼（gitのURLでリンクし、構成時に自動取得する）を検討し、ユーザーの了承（リンク先はCppBuildのSolutionを含むリポジトリとする）を得て実装した。実装時の判断は次のとおり。

- **既存の仕組みへの載せ方**：gitのリンクは、従来の依存（GUID）と参照一覧に、Solutionの記録 `git_sources` を加えたもの。参照は `deps/<名前>/<パス>/.cppbuild` を指すので、unlinkや依存解決は従来どおり動く。解決では、gitの記録（間接依存を含む）の置き場所を「最上位の依存探索ディレクトリ」と同じ扱いにした。最上位の場所が入れ子の登録より優先される既存規則がそのまま効き、リポジトリの途中にあるSolution（第3引数）も見つかる。
- **記録の単位**：1リポジトリ1記録（URLの末尾の `/` と `.git` を除いて同一視）。置き場所の名前は記録のキーとして保存するので、一度決まれば変わらない。新規はURLのリポジトリ名、同名の別URLには `-<URLのSHA-256先頭8桁>` を付ける。
- **間接依存と菱形**：gitでリンクしたSolutionが自分で記録したgitのリンク先を、幅優先でたどって一覧にする（`git_sources.walk`）。最上位の記録があればそれを使う。最上位に記録がなく、同じリポジトリの記録（コミットかパス）が食い違えばエラー。最上位の記録は `set_git_source` で作れる。以前取り下げた「手で参照を登録するAPI」とは別物で、依頼の「記録を更新する手段」で兼ねた。最上位が自動で間接依存を記録することはしない（記録は利用者が選んだものだけにする）。
- **リビジョンの解決**：40桁（SHA-256のリポジトリは64桁）のコミットIDはそのまま記録する。ブランチ名・タグ名は `git ls-remote` で解決し、既存のcloneが古くても古いコミットを記録しない。省略形のコミットIDだけは既存のcloneで解決する。新しくcloneする場合は、clone内で解決する。
- **取得の規則（PythonとCMakeで同じ）**：置き場所に `<パス>/project.json` があれば何もしない。比較だけ行い、HEADが記録と違えば警告する。置き場所がなければ `<置き場所>.cppbuild-fetch` へ `git clone --no-checkout` し、記録のコミットを `checkout --detach` し、Solutionがあることを確かめてから置き場所へ移す。失敗した一時ディレクトリは消す。置き場所があるのにSolutionがない場合は、上書きせずエラーにする。FetchContentは記録が変わると既存の作業版を切り替えるため使わない。
- **生成ファイル**：最上位の `CppBuildTopLevel.cmake` に、取得用の関数と全リンク先（間接依存を含む）の呼び出しを書く。内容は記録だけで決まり、PCに依存しない（URLが手元のパスなら、そのURLはそのまま書かれる）。取得はリンク先の `add_subdirectory` より前に行う。取り込まれた側の `CppBuildTopLevel.cmake` は読まれないので、取得するのは常に最上位だけ。
- **入れ子のplanは取得しない**：リンク先Solutionの生成ファイルを書くための `plan(other)` は、リンク先の依存探索ディレクトリへcloneしない（`plan(..., fetch=False)`）。取得は操作の入口（`engine.project_plan`・`solution_engine._prepare`）と `link_git`・`fetch_git_sources` だけ。
- **生成形式の版**（`cmake_files.FORMAT`）：各Solutionの `CMakeLists.txt` がディレクトリ属性 `CPPBUILD_FORMAT` を宣言し、取り込む側が `add_subdirectory` の後に確認する。他のリポジトリの生成ファイルが別の版のCppBuildで作られていれば、構成時に分かるエラーにする。この版の導入前に生成されたファイルも、属性がないためエラーになる（CppBuildで再生成すれば解消）。
- **見送った点**：パス指定でリンクしたSolutionの中のgitの記録はたどらない。サブモジュール・Git LFS・浅いclone。cloneの削除（`remove_git_source` は記録だけを消す）。リンクを外したときの記録の自動削除（間接依存を選ぶための記録と区別できないため、明示の `remove_git_source` とした）。

## 2026-10-01実装：CppBuildなしで使えるCMakeの生成

ユーザーは「CppBuildが生成したファイルだけで、CppBuildなしで構成・ビルド・.sln生成・実行・テストでき、持ち出しもできる」状態を目指す（ECOBuildの下書き依頼3と同じ目的）。Projectごとの独立CMake構成にはこだわらない。下の着地点・API影響の検討をユーザーが了承し、「実装が歪む箇所がなければ進める」と指示した。実装時の判断は次のとおり（検討時の記録はその下に残す）。

### 実装時の判断

- **生成ファイルを2つに分けた**：Solutionの `CMakeLists.txt` は所属Projectだけを並べ、誰に取り込まれても同じ内容にする。リンク先の解決結果（どのSolutionをどこから取り込むか）は最上位の視点でしか決まらないため、`CppBuildTopLevel.cmake` に分け、最上位のときだけ読む。他のSolutionから取り込まれるとき、リンク先Solutionの入口は自分の依存を取り込まない（最上位が依存の全体を解決して取り込む）。
- **必要なProjectだけ構成**：最上位はリンク先Projectごとに要求する形式を `CPPBUILD_REQUEST_<GUIDの16進>` で渡し、リンク先の入口は要求されたProjectだけを `add_subdirectory` する。リンク先のTESTは登録しない（`PROJECT_IS_TOP_LEVEL`）。表示だけの未参照Project（solution_folders）は `DISPLAY` を要求し、ターゲット単位で `EXCLUDE_FROM_ALL`／`EXCLUDE_FROM_DEFAULT_BUILD` を付ける。VSでは、`add_subdirectory(... EXCLUDE_FROM_ALL)` にするとそのディレクトリのターゲットが.slnから消えることを試作で確認したため、ディレクトリ単位では除外しない。2026-10-03修正（レビュー指摘）：表示だけのProjectからの依存要求は `DISPLAY_<形式>` として通常の要求と分け、その形式のターゲットだけを既定ビルドから除外する（使う側の要求と表示側の要求が同じ形式なら通常どおり作る）。表示だけの実行ファイル・TEST・DLLは、最上位が `add_subdirectory` 前に設定する `CPPBUILD_LINKED_OUTPUT` を使って `${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/_linked/<リンク先>` に出し、利用側の同名実行ファイルとの出力衝突（Ninjaの `multiple rules generate`）を避ける。
- **リンクは別名で**：利用側は `<Solution>::<Project>_<保存した形式>` をリンクし、提供側がその別名を選択形式のターゲットへ割り当てる。定義の前の別名を参照しても、CMakeは生成時に解決する（試作で確認）。これにより、提供側の形式の上書き（`<Solution>_<Project>_TYPE`）を利用側のファイルに書かずに済む。
- **形式ごとのターゲット**：ターゲット名は作成時形式が `<Solution>_<Project>`、他は接尾辞付きで固定。成果物名は通常Project名で、静的と共有を同時に作るときだけ、選択形式以外に `-static`／`-shared` を付ける（静的ライブラリとDLLのインポートライブラリの衝突を避ける）。
- **形式の選択の一本化**：CMakeの変数は1つなので、Python側も「`project_types`、なければ `ProjectBuildSettings.project_type`」を自分の形式とし、静的／共有なら静的・共有のリンクも切り替える規則に統一した。従来、作成時形式がインターフェースのProjectに `project_types` だけを指定すると自分はインターフェースのままだった点が変わる。`project_types` と `INTERFACE_LIBRARY` の明示の併用はエラー。
- **外部Solutionの設定の継承**：同じツリーで構成するため、`external_build_settings` がない外部Solutionは最上位の構成・アーキテクチャ・C++標準・ToolSettings・CMakeSettingsを引き継ぐ。従来は外部側が既定値（VS2022）のままで、Ninja等では互換性エラーになっていた。
- **リンク先Solutionのファイルも書く**：取り込むProjectの生成ファイルとリンク先の入口は、最上位の操作で書き直す（依存探索ディレクトリのcloneにも書く。内容は管理JSONから決まるため、最新のcloneなら差分は出ない）。リンク先自身の依存が解決できるときは、その `CppBuildTopLevel.cmake` と残りのProjectのファイルも書き、リンク先を単独でCMakeに渡せる状態にする。解決できなければそのままにする。2026-10-03修正（レビュー指摘）：リンク先の単独利用が参照する、さらに先のリンク先（最上位の操作では使わないSolution）のファイルも再帰的に書く。Solution同士の相互リンクに備え、訪問済みのSolutionは再計算しない。同じファイルは、より上位の視点で求めた内容を優先する。
- **保存しない設定の受け渡し**：ビルドツリー内の `cppbuild-cache.cmake` を `cmake -C` で渡し、すべての値を毎回明示する（前回の値がキャッシュに残る問題を避ける）。2026-10-02更新：CppBuildのツリーでも自動再構成は止めない（`CMAKE_SUPPRESS_REGENERATION` はOFF）。clean時の再生成禁止は、cleanのコマンド側で守る（VSはMSBuildのCleanを.vcxprojへ直接、Ninjaは `ninja -t clean`／成果物の削除。`cmake --build` はVS2026でZERO_CHECKを先に実行し再構成することを実試験で確認したため使わない）。
- **clean**：「AppのcleanはAppだけ」という当初の合意を維持した。VSはMSBuildの `Clean`（`BuildProjectReferences=false`）。Ninjaには単一ターゲットのcleanがなく、`ninja -t clean <target>` は依存先の成果物も消すため、File APIが示す成果物とオブジェクトディレクトリを削除する。共有依存の保護（依存解決して対象外の依存を残す処理）は、CMakeが消えた依存を作り直すため廃止した。
- **一意性の検査**：別のProjectが同じターゲット名になる場合と、同名の共有ライブラリ（DLL名の衝突）はSettingsError。同じSolution内のTEST Projectが別々の `googletest_archive` を指定した場合もエラー（FetchContentはツリーに1回）。2026-10-03：別Solutionの同名実行ファイルは、表示だけの側の出力先を分けて許す（ユーザー合意の案A）。DLL名の衝突は、出力先を分けても読み込み時に区別できないため、表示用でも従来どおりエラーとする。
- **成果物の特定**：`file(GENERATE)` の出力ファイルをやめ、CMake File APIの成果物一覧と `nameOnDisk` を使う。
- **残る制約**：構成は常にSolution全体で行う（他ProjectのCMakeの誤りで個別操作も失敗する）。旧Projectごとの`.cppbuild/output`は使わないが自動削除しない。M3aの試験 `tests/test_integration_candidate.py` は旧方式の技術検証の記録として残す（製品コードを使わない）。Linux/macOSでの実検証は未実施。

### 設計レビューへの対応（2026-10-03）

[全体設計レビュー](DESIGN_REVIEW_2026-10-03.md)の優先度の高い4項目を、公開APIを変えずに次のとおり実装した。

- **状態とロック**：操作ロックは、全体／個別の生成・ビルド・テスト・cleanで、Solutionと所属Projectの`.cppbuild/operations`を共通に取る（`storage.operation_lock`）。従来、Project.cleanは自分のProjectだけをロックしていた。状態の判定材料に、ツリーを決める設定（`information.tree_key`：Solutionのintermediate_directory、アーキテクチャ、CMakeSettings、ToolSettings）を加えた。`info()`はツールを起動しないため、生成器の解決結果ではなく設定値を使う。個別操作では、自分の設定が同じツリーを選ぶProjectだけを生成・ビルド済みとして記録する（全体操作は全メンバーの一致を事前に検査済み）。
- **表示とビルド参加**：Python側の要求を `Request(built, listed)` で表し、CMakeへは従来どおり `<形式>`／`DISPLAY_<形式>`／`DISPLAY` のリストで渡す（CMakeの変数はリストのため）。
- **名前の検査**：CppBuildなしで形式を切り替えられるため、各Projectが作り得るすべてのターゲット名と別名を検査する。CMake・GoogleTestの予約名（`ZERO_CHECK`、`gtest_main`など）も拒否する。実行ファイル・TESTと同名のDLLを一緒にビルドすると、実ビルドで`App.pdb`・`App.ilk`が1つになり上書きし合うことを確認したため、エラーにした（成功するが、デバッグ情報が壊れる）。同名のテストが2つのTEST Projectにあると、CTestが後のラベルを両方に付け、片方のテストで他方のテストも実行することを実ビルドで確認した。テスト名は実行時まで分からず事前に拒否できないため、`gtest_discover_tests` の `TEST_PREFIX "<Project名>."` で区別し、CppBuildの結果では接頭辞を外す。
- **生成ファイルの書き込み**：生成内容はplanごとに一度だけ確定し（`Plan._all_documents`）、全パスの所有を確認してから `storage.publish_documents` で書く。CppBuildが生成していないファイルが1つでもあれば何も書かず、書き込み途中の失敗では書き換えたファイルを戻す。ロックの範囲は、書き込むファイルのディレクトリから決める（リンク先の単独利用のために書く、さらに先のSolutionも含む）。
- **見送った点**：設定とソース一覧を操作の最初に固定する作り直し（`Plan`がSolution・Projectのオブジェクトを参照し、リンク先のplanを生成時に解決する構造は維持）。リンク先の単独利用用ファイルを生成できなかった理由を結果に返すこと（公開の結果型の変更が要る）。強制終了・電源断まで含む書き込みの原子性。

### CMakeの推奨に照らした見直し（2026-10-02）

ユーザーの依頼で、生成するCMakeと運用を一般的な推奨と照らし合わせ、無理なく直せる箇所を修正した。

- 修正：GoogleTestの選択肢を `FORCE` 付きキャッシュから通常の変数へ（CMP0077、利用者の `-D` を上書きしない）。CTest標準の `BUILD_TESTING`（`option`、既定ON）を設け、OFFならTEST Projectを構成せずGoogleTestも取得しない。実行ファイル・TESTの利用要件をPUBLICからPRIVATEへ。
- 残した非推奨・非標準の箇所（既存機能や運用の維持のため）：
  1. 依存探索ディレクトリのclone（他リポジトリの作業ツリー）へ生成ファイルを書き込む。最新のcloneなら差分は出ない。代替は「書かずに古ければ警告・エラー」で、運用方針の判断が要る。
  2. NinjaのProject単位cleanは、CMake内部の配置（`CMakeFiles/<target>.dir/<構成>`）を前提に削除する。Ninja標準の `-t clean <target>` は依存先も消すため、「AppのcleanはAppだけ」の合意を優先した。
  3. （2026-10-02一部解消）標準の `BUILD_SHARED_LIBS` を全体の既定値として尊重し、Projectごとの `<Solution>_<Project>_TYPE` はライブラリ単位の上書き（`ZLIB_BUILD_SHARED` と同じ形）とした。静的と共有の同時利用（利用側ごとに別形式でリンクする既存機能）は標準にない独自の仕組みとして残る。
  4. （2026-10-02解消）CppBuildのビルドツリーでも自動再構成を止めない。cleanが再構成を起こさないよう、全体cleanはNinjaでは `ninja -t clean`（build.ninjaを作り直さない）、VSでは `ALL_BUILD.vcxproj` へのMSBuildのClean（カスタムビルドを実行しない）、Project単位のcleanは各.vcxprojへのMSBuildのClean（参照先を処理しない）とした。CMakeのキャッシュにあるMSBuildを直接呼ぶ点は独自だが、MSBuild標準のターゲットだけを使う。
  5. リンク先Solutionをソースツリー外から `add_subdirectory("../STL" ...)` で取り込む。外部プロジェクトは `find_package`／`FetchContent` が一般的だが、ソースごと同じ構成でビルドする方針のため。
  6. （2026-10-02解消）別名は `::` を1つにした（`STL::Containers_static`）。
  7. ImportedLibraryの未提供の構成は存在しないパスを指す。標準の `MAP_IMPORTED_CONFIG_<構成>` は、未提供の構成で別の構成のバイナリーを黙って使うか、マルチ構成の生成時点でエラーになるため、従来の方式を残した。2026-10-02：パスを `<ビルドディレクトリ>/cppbuild-missing/<名前>-has-no-<構成>-file` とし、リンクエラーが原因を示すようにした。
  8. Solution間の変数（`CPPBUILD_REQUEST_<GUID>`・`CPPBUILD_REQUESTED`・`CPPBUILD_LINKED`）による取り込み範囲の受け渡しは、CppBuild独自の取り決め。2026-10-02：CppBuild以外のプロジェクトから `add_subdirectory` された場合（`CPPBUILD_LINKED` なし）は、全Projectとリンク先を最上位と同じく追加するようにした（以前は何も追加されなかった）。

### 検討時の記録

以下は実装前の検討記録。

- 現状の障害：生成CMakeは `.cppbuild/output/...` 内にあり絶対パス。依存はIMPORTED（ビルド済みファイル参照）で、ビルド順・実行時PATHはPython側が担う。
- 着地点：CppBuildは「普通のCMakeプロジェクトを生成・管理し、操作の窓口になる道具」とする。Solutionの入口CMakeLists.txt＋ProjectごとのCMakeLists.txt（add_subdirectory）をソースツリー内に生成し、パスは相対。ビルドツリーはSolution×生成環境ごとに1つ。個別操作は同じツリーでのターゲット指定（構成はSolution全体、ビルドは個別）。.slnはCMakeが直接生成する1つ。
- 別Solutionのリンク：ソース取り込み（`if(NOT TARGET)`＋`EXCLUDE_FROM_ALL`）を標準とし、GUID・link_solutionの使い方は維持。Solutionをまたぐビルド結果の共用は失う（必要ならパッケージ方式を将来追加）。
- 成果物共用の本来の目的（全体／個別の二重ビルド回避、個別更新の全体反映）は維持される。失うのは個別構成の独立性（他ProjectのCMakeエラー・構成時間の影響）。
- 維持する条件の変更案：「Projectごとの独立CMake構成」→構成はSolution単位、「共有依存の保護」→廃止。成果物共用・ビルド設定の非保存・GUID解決・clean前の再生成禁止は維持。
- 未決：Project単体clean（Ninja）、同一ライブラリの静的／共有の同時利用、`PROJECT_IS_TOP_LEVEL` によるProject単独入口、生成CMakeのコミット対象化、未コミットの出力先機能の扱い。

### 既存APIへの影響（コード確認による机上チェック、未検証）

- そのまま使える：Solution.create/open/get_project/projects/add_project/remove_project、settingsのget/save/reload、link_project/link_solution/unlink/link_package/link_cmake_source/link_imported_library、set_pch/clear_pch、ファイルテンプレート、add_file/remove_file/move_file、on/off、check_environment、dependency_directories・MissingDependenciesError、solution_folders、Solution.build/run/test/rebuildとbuild_projects/run_projects/test_projects・実行並列設定、5つのProjectType、configurationの個別指定（マルチ構成ツリーの `--config` で対応）。
- 動くが意味・挙動が変わる：
  - Project.update と auto_update付きファイル操作は、Solution全体を構成する。他ProjectのCMakeエラーで失敗し得る。UpdateReportのbuild_directory・solution_fileはSolutionのものになる。
  - Project.clean／rebuild：VSはMSBuildのProject単位Clean。Ninjaは標準手段がない。共有依存の保護はなくなる。Solution.cleanのbuild_projects選択はVSのみ維持可能。
  - architecture・cmake・toolsのProject個別指定：その生成環境のSolutionツリーで全体を構成する。
  - cpp_standard・project_type・SolutionBuildSettings.project_types：キャッシュ変数（`-D`）に対応する。値を変えるとツリーの再構成と再コンパイルが起き、全体／個別で交互に変えると毎回再ビルドになる。
  - info()：生成状態がProject単位で独立しなくなる。
  - move_project：キャッシュ退避は不要になる。ただし、移動したProjectをリンクする別Solutionの生成CMakeは、そのSolutionの再生成まで古いままになる。
  - TemplateTools：ソースツリー内の生成CMakeの除外または再生成が必要。
  - 実行時のPATH追加は不要になる（DLLを共通出力フォルダーに集約）。
- 機能しなくなる・制限が必要：
  1. 同じライブラリを利用側ごとに別形式でリンクすること（App1は静的、App2は共有など）。1つのツリーでは形式がライブラリごとに1つなので、Dependency.project_typeの食い違いはエラーにする。現行は形式別ツリーで両立していた。
  2. ProjectBuildSettings.intermediate_directory：ツリーが1つになるため意味を失う。SolutionBuildSettings.intermediate_directory（`-B`）へ一本化する。artifact_directoryは対象プロパティで残せるが、DLLの共通フォルダー集約と両立しない指定は実行時に解決できない。
  3. ProjectBuildSettings.googletest_archive：FetchContentを入口で1回宣言するため、実質Solution単位になる。Projectごとに異なる値はエラーにする。
  4. Solutionをまたぐビルド結果の共用：外部SolutionのProjectは、利用側ツリーで再コンパイルされる。
  5. external_build_settings：生成環境・構成の一致はもともと条件。1つのツリーでは、外部Solution別に変えられるのはcpp_standard等の対象単位の値に限られる。

### 追加検討（同日、ユーザーとの質疑）

- 共有依存の保護は、IMPORTED方式で依存先の成果物を消すと、利用側のツリーから再生成できない問題への対策だった。1つのツリーではCMakeが依存先を自動で再ビルドするため、保護は不要になる（消えても再ビルドの手間だけ）。VSの個別Cleanは `BuildProjectReferences=false` で依存先を巻き込まない。
- 形式の同時利用は、形式別ターゲットの生成で維持できる見込み（zlib/zlibstaticと同様）。要求された形式だけを生成し、ターゲット名に形式を付け、静的ライブラリと共有ライブラリの取り込み用.libが衝突しないよう出力名を分ける。project_typesによる切り替えは、リンク先ターゲットの選択（キャッシュ変数）になり、両形式のビルド結果がツリーに残るため、切り替え直しでの再コンパイルを避けられる。先の「機能しなくなる」1は撤回候補。
- テストはProjectごとのままで、Project.testはラベルで自分のテストだけを実行する。Solution単位になるのはGoogleTest本体の取得（googletest_archive）だけ。
- move_projectの対策案：別Solutionは個々のProjectではなく、リンク先Solutionの入口をadd_subdirectoryで取り込む。Solution内の配置変更はそのSolutionの生成ファイルだけで閉じる。入口のproject()・enable_testing・FetchContentは `PROJECT_IS_TOP_LEVEL` で分岐し、Projectは `EXCLUDE_FROM_ALL` で必要な分だけビルドする。欠点は、リンク先Solutionの無関係なProjectも構成されること。
- Solutionをまたぐビルド結果の共用は、要件（FEATURE_REQUESTS.md）に記載がない。Projectごとのツリー方式の副産物だった可能性が高い。依存探索ディレクトリ（deps/へのclone）では、利用側ごとに別コピーになるため、もともと共用されにくい。

## 2026-10-01追加：生成領域と成果物の出力先

- Projectにintermediate_directory／artifact_directory、Solution自身にintermediate_directoryを設ける。管理ファイルの場所は変更せず、Solutionからの出力先継承は行わない。
- 既定値は各所有者の`.cppbuild/output/intermediate`とProjectの`.cppbuild/output/artifacts`。指定値は所有領域を作るルート。所在・GUID・生成環境・種類を10桁の識別子へまとめる。中間領域の識別には成果物の出力先も含める。ハッシュ衝突は所有マーカーの照合で拒否する。
- 初期実装の深い階層ではWindowsのコンパイラ検査がパス長制限に達したため、短い1階層へ変更した。さらに同じツリーでOutDirを変更するとMSBuildが旧成果物を消すことを実確認したため、成果物の出力先変更時も別ツリーを使う。
- cleanは現在の設定の既存ツリーを使い、未生成なら対象なしで成功。旧出力領域の削除API・自動削除は追加しない。
- ソース配下の出力領域は所有マーカーで走査・テンプレートから除外する。再openや指定変更後の古い領域も除外する。ファイル操作APIから生成領域内の変更を拒否する。
- Project移動は出力パスを補正し、外部の出力先を維持する。所在が変わるためキャッシュ識別子を更新する。旧領域は保持し、旧配置generated/buildの退避は従来どおり。
- 外部Solutionの別インスタンスに指定した非保存Project設定が依存操作へ伝わらない点は既存設計の制約。暗黙のグローバル共有や管理JSONへの保存は導入しない。外部Projectの個別出力指定が必要になった場合は、操作へ渡す設定APIを別途設計する。

## 2026-10-01：依存探索ディレクトリとリンク形式の切り替え

- ECOBuildからの依頼。ユーザーの指示で、手動の参照登録（pin）案は依存探索ディレクトリによる自動解決に差し替えた。依存解決結果の取得は、今回の範囲外とした。
- 探索ディレクトリの設定は、Solutionの構成情報なので管理データとして保存する。空のときは書き出さず、schema_versionも上げない。既存リポジトリの管理ファイルに差分を出さないため。
- 所在の優先順は「最上位の参照一覧 → 最上位の探索ディレクトリ → 入れ子の参照一覧」。明示リンク（作業版）を探索結果より優先する。
- 見つからない依存は例外で即停止せず、解決を最後まで進めて一覧で返す（ECOBuildがcloneの対象を判断するため）。
- 形式の切り替えは静的⇔共有に限定した。インターフェースへの切り替え・インターフェースからの切り替えは、リンクエラーや無意味なバイナリ依存になるため行わない。外部Project向けの指定は、所在ではなくGUIDで、最上位の操作設定に置いた（最上位優先の規則と一致させるため）。
- テンプレートでは、探索ディレクトリの中身（clone）を除外する。探索ディレクトリ内への参照は、新しいSolutionの同じ位置に付け替える。

## 2026-09-30：生成器・コンパイラ切り替えの実装判断

- ユーザー合意：生成器の省略時はOSごとの固定値。VSを前提にしていた箇所はすべて修正対象。cleanはNinjaの挙動（CMake標準のclean）へ統一。検証は手元の環境で行う。
- .slnxはプロジェクトを項目名ではなくファイル名で識別する。このため、同名で種類の違うProjectをMSBuildの `/t:` で選べない。VS2026の全体ビルドは、Ninjaと同じPython側の依存順ビルドにし、.slnxはIDE表示用とした。VS2022（.sln）は従来のMSBuild選択ビルドを維持する。
- 既存のVS2022ツリーの旧所有マーカー（architectureのみ）は有効とし、次の生成で新形式へ更新する。
- 依存先の成果物は `file(GENERATE)` の出力ファイルで特定するため、既存ツリーは一度update/buildしてから依存側で使う必要がある。
- Linux/macOS向けの処理（RPATH／LD_LIBRARY_PATH、POSIXの上書きしない移動、AppleClangのアーキテクチャ指定、GCC/Clangの照合）は実装したが、実環境では未検証。

## 2026-09-30：次の開発テーマ・生成器とコンパイラの切り替え

ユーザーはビルドを含む通常操作をWindows限定にしないことを希望。調査・API案・次の判断と実装順序は [対応計画](CROSS_PLATFORM_PLAN.md) に集約した。CMakeSettings、Environment.generators、architectureの整理等は未実装の提案。現行APIへ実装済みとして転記しない。次の実装依頼では設定・互換性・生成結果・対応範囲を具体化してから現行設計へ反映する。

## 2026-09-30：ライブラリ形式の改名と切り替え制限

- ユーザーとの相談と修正依頼に基づき、HEADER_ONLYをINTERFACE_LIBRARYへ改名する。ヘッダー内の実装完結を要求せず、.cppがあっても自身のコンパイル対象にしない形式とする。
- ライブラリは静的・共有・インターフェースの3形式を相互選択可能とし、実行ファイルとTESTは固定する。3形式の事前登録操作は不要とする。リンク側の要求形式は自動変更しない。
- 具体化：initial_typeを変更不可の作成時管理情報として保存し、非保存の選択を省略した際に使う。これによりライブラリ3形式を自動登録しても、従来の単一形式Projectが明示選択なしで使える。形式別設定の不足は既定値で補う。保存したビルド設定から選択を復元する仕組みは導入しない。
- 旧複数ライブラリ設定には作成時形式の情報がないため、静的→共有→インターフェースの順で既存形式を採用する。旧混在形式は設定を切り捨てずエラーにし、旧版で整理する。これらは移行時の具体的な制約。
- 全体.slnの未参照Projectの表示は作成時形式を使う。3形式を一律に生成すると、インターフェース用途のProjectへ静的・共有のソース要件を課してしまうため。参照されている各形式の表示は維持する。
- 検証・進捗・コミット状況はIMPLEMENTATION_STATUS.mdを参照。

## 2026-09-30：追加機能の検討候補（未合意・未実装）

後続の追加指示：この検討後、通常cleanから事前のCMake構成・再生成を除く実装修正が依頼された。API追加・キャッシュ全削除は引き続き未採用。以下の現状説明は検討時点の記録として残す。修正後はUSAGE.mdのクリーン節とIMPLEMENTATION_STATUS.mdを参照。

ユーザーの現状見直し依頼に基づき、現行実装と制約から整理した。既存要件の未達を意味せず、API名や採用範囲は未決定。

- 最優先：CMakeを起動せずに所有する生成物・キャッシュを削除する機能。現在のcleanはengine/solution_engineで生成後にMSBuild Cleanを呼ぶため、構成失敗や移設による所有マーカー不一致からの復旧には使えない。Project別・Solution別の対象、全種類・architectureの選択、削除予定の確認と結果報告を設計する。Debug/Releaseが同じビルドツリーを使う点を明示し、設定・ソース・外部依存は保持する。
- 高：不要ファイルの整理。Project移動で退避したrelocations、実行ごとに増えるCTest JUnit XML、移行後の未参照種類設定を候補として列挙し、現行参照・所有範囲を確認して明示的に削除する。退避物や旧設定の削除は復旧に使える情報を失うため、通常cleanへ暗黙に含めない。
- 高：依存解決結果の取得。GUID・種類・実際の所在・利用元・最上位参照による選択理由を構造化データで返す。保持済み情報だけを返すinfoの意味は維持する。解決時の設定移行・書き込みの扱いも設計が必要。
- 高：外部参照の所在更新。GUIDと解除用IDを保持したまま、指定された新しいSolution内に同じGUIDが存在することを検証し、参照を共用する利用元へ一括反映する。現状は全利用元でunlink/relinkが必要。自動探索は前提にしない。
- 中：処理中ログ通知、実行時間制限、キャンセル。現在のRunReport.wait(timeout)は待機の時間制限であり実行停止ではない。CMake/MSBuild/CTest/アプリの子プロセス終了、ロック解放、部分結果と状態更新を一緒に設計する。
- 中：テスト名による選択、失敗ケースの再実行、テストごとの時間制限。現行はProject単位の選択と並列数のみ。0件や古い結果を成功扱いしない既存規則を維持する。
- 用途に応じて検討：警告レベル・警告のエラー化・コンパイル／リンクオプション・MSVCランタイム等のビルド設定。現在の型には専用項目がない。保存／非保存と依存先への伝播を定義する必要がある。

実装順の推奨は、生成物・キャッシュ削除 → 不要ファイル整理 → 依存解決結果 → 外部参照の所在更新。後続は利用場面に応じて選ぶ。Solution全体削除・単一Projectテンプレート・パッケージ配布等の既存対象外事項は今回の優先候補に含めない。VS IDEのGUI確認・別PCでの移設試験は追加機能とは別の検証課題として残る。今回、製品コードの変更とテスト実行は行っていない。

> 2026-09-17 実装開始後の追記：M1（管理モデル・設定保存・非保存ビルド設定）を実装し、自動テスト13件を確認。最新の進捗・検証・次の作業は[実装記録](IMPLEMENTATION_STATUS.md)を参照。以下の「未着手」「実装はまだ開始しない」は設計整理時点の記録であり、今回の実装依頼を制限しない。設計の合意状態は維持する。

更新日：2026-09-17。現行APIは[API_DESIGN.md](API_DESIGN.md)、詳細案は[BUILD_DESIGN.md](BUILD_DESIGN.md)。実装前の設計資料。

## 今回の進捗

2026-09-18 M6実装補足：テンプレート参照はPath、置換は本文の単回置換、登録はSolution内共有素材を対象とした。イベントは登録順・同一登録の再帰抑止・Project別更新集約、失敗時の部分結果を実装。環境診断は実操作とToolSettingsを共用し、infoは外部走査をせず観測済み状態を返す。具体的な規則・制約は[API設計](API_DESIGN.md)のM6実装補足を参照。以下の設計時点の未確定事項と実装選択を区別する。GoogleTestのオンライン取得は承認後に成功し、通信保留は解消した。

実装開始後：M1・M2を実装し、管理設定・独立ProjectのVS2022生成・実ビルドを25件の自動テストで確認した。
全体統合方式と共有依存のcleanは未実装。詳細な制約・実測結果は[実装記録](IMPLEMENTATION_STATUS.md)参照。
以下の箇条書きは設計整理時点の履歴。

M3a追加：include_external_msproject単独では`cmake --build --target App`が.slnの依存順を使わないことを実測。
全体.slnをMSBuildへ渡すCMake custom targetで回避可能。ただし全体／個別の成果物が共用になり、
個別cleanが全体で使う同一成果物も削除する。既存の所有範囲要件との関係は未合意のため、
[判断資料](INTEGRATION_DECISION.md)に記録した。その後、Project別所有・全体／個別共用に合意しM3bを実装した。

- 最新合意：親設定継承・実行順の既定・固定版FetchContentでのGoogleTest導入は現時点の案で暫定採用。後から変更できる細部を理由に設計確認を繰り返さず、既存方針内で具体化する。実装・自動テスト・CMake実ビルド検証は依然未実施。

- 追加確認済み：ビルド設定の入口はメソッド。ビルド設定は外部ファイル化せずメモリ上だけで扱い、Solution／Project管理設定は保存する。複数種類対応では種類別の管理設定ファイルを持つ。実行順は既定を用意し設定で変更できるようにする。
- SETTINGS_DESIGN.mdに設定の区分、継承・種類別ファイル・実行順とGoogleTest導入を整理した。固定版FetchContentによる取得方式は暫定採用済み、取得・実ビルド検証は未実施。

- updateの主目的はソース配置からVSファイル一覧・フィルターへの反映。各Projectに独立したCMake入口・ビルドディレクトリ・キャッシュを持たせて個別更新する方針を追加確認済み。共通ツリー全体再生成を個別updateの前提とする提案は撤回した。実装・実ビルド検証は未実施。

- 現行APIと要件の本文をSolution／Projectへ統一した。メソッド・型・設定項目の改名は整合案として記載し、元の基本操作の合意と区別した。
- updateのVS2022生成要件、Project側の詳細設定、Solution側のビルド・実行対象選択を本文に統合した。古い「通常は保存済み設定、optionsで今回だけ上書き」の説明を現行の基本モデルから外した。
- 全体／個別の生成入口・ビルドツリー、設定の受け渡し・保存、キャッシュ・cleanをBUILD_DESIGN.mdに具体化した。新規案はまだ未合意であり、動作検証済みとは扱わない。
- 整理前の全文はreferences/DESIGN_BEFORE_DETAIL_2026-09-17.mdに保存した。既存の参考資料は改変していない。

## 追加判断を要する点

| 項目 | 今回の案と残る判断 |
| --- | --- |
| 全体／個別更新 | 個別更新は独立したCMake構成で行う方針を確認済み。個別.vcxprojを全体.slnへ統合する方法・更新順序・構成対応は未確定。include_external_msprojectを候補として検証する。 |
| 初回・外部変更 | 未生成の依存Projectの準備、参照先パス・GUID・構成変更、全体.slnの更新が必要となる条件を設計する。 |
| 比較候補 | 共通ツリー案は未採用。全体用.vcxprojを別生成する旧案も全体統合方式としては未合意。 |
| 設定の入口 | メソッドで受け取ることは確認済み。set_build_settingsという具体名、コピー保持・置換・未設定時の動作を具体化する。 |
| 保存との関係 | ビルド設定は保存しないことを確認済み。管理設定save/reloadで実行用設定を変えない案。生成物を保存・復元元にしない。 |
| 保存形式 | 管理設定のみ全体solution.json／個別project.jsonとschema_versionの案。種類別ファイルの採用は確認済みで、名前・配置・原子的な複数ファイル更新を設計する。 |
| 全体の構成 | 親Solutionからの継承を提案。子の明示値との優先順位、対象項目、継承指定の置き場所、依存間の不整合診断を具体化する。 |
| 種類 | 種類別管理ファイルを持つ。保存する構成定義と保存しないビルド時選択の項目境界、CMake targetと.vcxprojの対応、HEADER_ONLY表示を設計・検証する。 |
| 実行順 | 既定と設定による変更を用意することは確認済み。依存順ビルド・一覧順の逐次run等を既定案とし、並列・終了待機・失敗後継続の具体値を整理する。 |
| GoogleTest導入 | 固定版FetchContentを暫定採用済み。版・配置・独立Project間の再利用・CRT整合・オフラインの指定経路を具体化する。 |
| clean | 別ツリーを消さない構成。対象Projectだけの成果物削除・共有依存保護についてMSBuildの挙動と生成物一覧の実検証が必要。 |
| 外部依存 | 設定からCMakeに変換するデータ型、解除後のキャッシュ・副作用、取得方法を設計する。 |
| テスト参加 | test_projectsをSolution設定に置く整合案。実行とテストの設定データの分割は未確定。 |
| パス・移動 | Project外のソース・出力、Project間移動、生成物の所有検証と衝突規則が必要。 |
| 結果・状態 | 生成範囲ごとの未反映状態、部分成功、古い成果物の識別、プロセス未起動時の例外区分を具体型に落とす。 |
| テンプレート | 参照型、置換の不足値・範囲・エスケープ等は未確定。 |
| イベント | 更新の集約、再帰制御、発火順とコールバック失敗時の扱いは未確定。 |

## 次の作業

1. 確認済みの個別更新方針に沿い、全体.slnへの統合と依存・構成対応を具体化する。メソッドによる設定、ビルド設定非保存、種類別管理ファイルという合意を前提とし、継承と実行順の詳細を整理する。細かなフィールドまで個別承認を要求しない。
2. 開発を進める際は、Solution作成 → Project追加 → 管理設定保存・再読み込み → ビルド設定受け渡し → 更新 → ビルドの最小構成を実装する。暫定採用した事項について再度の合意を開始条件にしない。
3. AppがMathに依存し、独立したToolもある構成で、全体生成・個別生成・依存ビルド・個別cleanの範囲を実検証する。
4. 生成後にファイルを追加し、.vcxproj.filters、別ツリーの未反映状態、失敗結果を検証する。静的／共有・GoogleTestはこの基礎の後に進める。

## 検証状況

2026-09-18 M5実装時の具体化・制約：

- GoogleTest 1.14.0 ZIPをSHA256固定で使用。ローカルZIP経由のFetchContentを検証し、オンライン取得はユーザーの許可後に再確認する。初回取得ソース・バイナリは各Projectの所有ツリー内に置き、独立Project間のキャッシュ共用は行わない。
- MSVCの既定DLL CRTに合わせてgtest_force_shared_crtを有効化。CRT選択を公開設定として追加する場合は両者の整合を再検証する。
- CMake 4.2のGoogleTest列挙はJSON出力に絶対パスを渡すため、日本語パスとGoogleTestのWindows narrow fopenの組合せで失敗した。4.2以降では列挙だけ`--gtest_output=`を指定し、標準出力から列挙するCMake標準のフォールバックを使う。実行結果はCTest自身が生成するJUnitを読む。
- 非同期runは監視用スレッドを保持する。デタッチ・キャンセル・実行中のclean/rebuild同期は今回の実装対象外。利用側はRunReport.wait()後に成果物変更を行う。
- 今回の具体化は実装選択であり、設計の未合意事項を一括して確認済みへ変更するものではない。

- 実施：現行文書の名称・参照リンク・API一覧・旧方針残存を確認。CMake公式資料でVS2022 generator、source/build tree、構成選択、source_group、File APIを調査。
- 実施：GoogleTest公式のCMake導入例、CMakeのGoogleTest・FetchContent・CTest公式資料を確認。ダウンロードや実行は行っていない。
- 未実施：ライブラリ実装、自動テスト、CMake生成・C++実ビルド、VS2022での表示確認、MSBuild cleanの所有範囲検証。
- 公式機能の存在と、このライブラリの設計が動くことは別。推奨案は利用上の影響を確認してから実装する。

## 2026-09-28：Projectの配置変更の調査

同一Solution内でProject名を維持したフォルダー移動を調査。専用APIは未実装で、公開APIだけでは既存Projectの登録パスを変更できない。`solution.settings.save()`はprojectsの変更を拒否し、`add_project()`は既存の個別管理ファイルを拒否するため、remove/addによる再登録も代替にならない。

現実装で手動移行する場合の必要作業（移行の一連の実VS試験は未実施）：

1. ビルド・実行・設定更新を止め、設定をバックアップする。移動先は同じSolutionの配下で、Solution直下そのもの・全体.cppbuild内・他Projectとの同一または入れ子配置を避ける。
2. ソースと個別`.cppbuild/project.json`・`.cppbuild/types`等の保存設定を一緒に移動する。全体`<Solution>/.cppbuild/project.json`の`data.projects[Project名]`を新しい配置へ修正する。schema_version=1はSolutionルート相対、2はこの設定ファイルの親ディレクトリ相対。現在の全体設定ファイル名はsolution.jsonではない。
3. 移動したProjectの`.cppbuild/build`は旧絶対パスの所有マーカーを持つため、管理範囲外へ退避するなどして再生成させる。全種類・全アーキテクチャが対象。`.cppbuild/generated`も再生成対象として退避できる。管理設定を含む`.cppbuild`全体は削除しない。clean/rebuildは生成を先行させるため所有不一致の解消には使えない。
4. Project内の相対ソース・include・PCHは内部配置を維持すれば変更不要。Project外への相対参照は点検し、CMakePackage/CMakeSource/ImportedLibrary等の参照先を維持する。現行link_solutionは所属Solutionの参照一覧へ設定ファイル基準の相対パスを保存する。非保存のgoogletest_archive等の実行時パスも配置に合わせる。
5. Solutionを再openするかsettings.reloadし、get_projectで移動後のオブジェクトを取り直す。移動対象は新オブジェクトになるため個別の非保存ビルド設定を再設定する。再openなら全体設定・各Projectの非保存設定・イベント登録も再設定する。
6. solution.updateで依存利用側と全体.slnを含め再生成し、必要構成のbuild/testを確認する。個別updateだけでは利用側・全体.slnは更新されない。内部依存はProject名参照なので名前を維持すれば付け替え不要。他Solutionから利用されている場合はそちらも再生成する。パスを直接記した利用スクリプトや外部CMakeは別途点検する。

一時データで確認：公開saveのパス変更拒否、移動後の旧登録でopen失敗、登録JSON修正後のreloadと内部依存解決、旧Projectオブジェクトの無効化、個別非保存設定の初期化、旧所有マーカーによるupdate拒否、remove後の既存設定再add拒否。所有マーカーは実装と同じ形式で作成し、CMakeは起動していない。

今後APIを追加する場合は、実フォルダー移動と移動済み参照の更新の責務、失敗時の復旧、外部参照の維持、キャッシュの退避、非保存設定の引き継ぎを設計する。API名・仕様の合意や実装は今回の調査に含めない。

## 2026-09-28：Project移動APIの実装

上記調査後の追加依頼に基づき、`solution.move_project(name, destination, *, auto_update=True)`を実装した。先行する「専用API未実装」「手動修正」の記録は調査時点の状態。既存のファイル操作に合わせFileOperationReportを返す。設定保存後の再生成失敗は移動完了と区別し、pending_update/update_errorで表す。

- 同じSolution内の実フォルダー移動を対象とし、名前と既存Project/Settingsインスタンスを保持する。移動済み参照の修復・別Solutionへの移管・名前変更は対象外。
- 移動前に保存設定の競合、配置とリンク、管理データの妥当性を検査。Project内部を指す型付きパスを新位置へ、外部への相対パスを元の参照先を維持するよう変更する。同じSolution内の参照元設定も補正する。任意文字列や外部Solutionの設定は自動変更しない。
- 全種類・全アーキテクチャのgenerated/buildを全体.cppbuild/relocations/<id>へ退避する。管理設定とソースは保持し、退避先は結果のchanged_pathsに含む。退避キャッシュは自動削除しない。生成物を通常の素材として直接参照する独自設定の継続は保証せず、再ビルドを要する。
- 設定・全体操作ロックを取得し、検出した個別操作ロック・未完了run・イベントコールバック中の呼び出しを拒否する。同時操作の完全な同期機構ではないため、他プロセス／スレッドの操作と非同期runは停止・完了させて使う。
- renameや設定保存の例外では変更済み設定・移動・キャッシュを逆順で復元する。復旧I/Oも失敗した場合は現在位置・退避先を例外に含める。プロセス強制終了・停電まで保証する永続トランザクションではない。
- 個別の非保存ビルド設定とイベント登録を保持する。パス補正後もビルド設定は保存しない。移動後のキャッシュ観測状態はstaleにし、自動更新ではsolution.updateで利用側と全体.slnを再生成する。他Solutionの利用側のupdateは呼び出し側の責務。

検証結果・残作業はIMPLEMENTATION_STATUS.mdの同日実装記録を参照。
## 2026-09-29：ソリューションフォルダーの実装選択

- 表示設定は既定Noneで旧表示を維持。設定時のみ外部Solutionの全所属を再帰的に集める。未参照の複数種類Projectは明示種類がなければ全種類を表示する。
- 全所属の表示にはCMake構成が必要。表示のみのProjectも構成失敗・依存不正・構成不一致で全体操作を失敗させる。コンパイルしないことと構成不要は区別する。
- CMakeのUSE_FOLDERS/FOLDERで階層化。表示のみのノードはEXCLUDE_FROM_DEFAULT_BUILDとEXCLUDE_FROM_ALLを併用し、VS既定参加とCMake ALL_BUILDの双方から除く。実依存ノードには適用しない。
- MSBuildの対象名はフォルダー階層を含め、対象名中の特殊文字をMSBuildの規則で変換する。[Microsoftの仕様](https://learn.microsoft.com/en-us/visualstudio/msbuild/how-to-build-specific-targets-in-solutions-by-using-msbuild-exe)と[CMakeの仕様](https://cmake.org/cmake/help/latest/prop_gbl/USE_FOLDERS.html)を参照。
- 同名の外部Solutionは正規化済みの所在パス由来のハッシュをフォルダー名に付けて区別。生成target名は既存の一意な名前を維持する。空の仮想フォルダーは生成しない。外部Solution側の表示設定を利用側へ取り込まない。

## 2026-09-30更新：GUIDによるリンクの実装選択

- ユーザーの追加指示によりlink_solutionのname引数を削除し、リンク対象の識別をGUIDへ統一する。専用の参照登録・削除APIを増やさず、一覧確認にはsettings.get().referencesを使用する。同一Solution内link_projectの呼び出し方は維持する。
- GUIDはProjectの管理上の識別子であり、CMakeが生成する.vcxprojのProjectGuidとは別。Project作成ごとに新規発行し、保存・移動では保持する。GUID・種類が同じなら生成対象は共用する。
- 保存形式をschema_version=3へ更新。Dependencyはproject_guidとproject_typeだけを持ち、Solutionのreferencesは対象GUIDをキーにする。外部所在は設定ファイル基準の相対パスを維持する。同じGUIDの異なる所在やローカル・外部の重複登録は拒否する。外部Solution全体の移動先をGUIDだけで探索することはしない。
- 旧別名は保存済みの対象GUIDで統合し、解除用dependency_idはすべて残す。移行由来の重複依存を一つ解除しても、残る利用があれば参照一覧を保持する。新規の同GUID・同種類リンクは二重追加を拒否する。
- 旧設定の移行はopen/reload時に行う。循環依存で移行が再帰し続けないよう、外部側のGUID付与はリンクの移行と分離し、未解決の旧リンクはバージョン2の文書で保持する。循環は依存解決時に診断する。GUIDが未保存の旧外部リンクには参照先が必要で、GUID保存済みの旧別名は参照先がオフラインでも移行できる。
- 参照一覧と利用元の更新にはSolutionおよび全所属Projectの設定ロック・指紋を使用し、古い利用元一覧で共有参照を消さないようにする。複数ファイル公開の通常失敗では復元する。強制終了後の自動復旧は含めない。
- 全体.slnのProject表示とSolution名によるフォルダー表示を維持し、GUIDを共有するProjectは一度だけ表示する。
- 追加指示により、依存解決では操作を始めた最上位Solutionの参照一覧にあるGUIDの所在を、入れ子の依存先の登録より優先する。依存先が同梱したコピーを利用側の版に揃えるための規則で、保存済みの参照一覧は書き換えない。最上位に登録がない同じGUIDの異なる所在は従来どおりエラー。最上位のローカルProjectと同じGUIDを入れ子側が外部参照する場合は対象外で、従来どおりエラーとする。依存先の生成物は選ばれた所在で再生成するため、最上位からの操作と依存先単独の操作を交互に行うと再構成・再ビルドが発生する。

## 2026-09-30：設定ファイル基準のパス保存

- schema_version=2を導入し、保存された型付きパスを設定ファイルの親ディレクトリ基準に統一する。APIのSolution／Projectルート基準は変えず、入出力時に変換する。旧版の基準を誤解釈しないようschema_version=1を読み分けて移行する。
- 絶対パスを保存する旧方針を変更。外部依存も相対化し、別ドライブ／別共有で表現できない場合はエラー。GUIDだけで旧絶対パスの移設先を推測することはしない。旧設定は移設前に移行する。
- テンプレートの一時作成先と最終公開先では外部対象までの相対距離が異なるため、公開先基準の文書を作ってからrenameする。内部コピー対象は新しい内部配置を参照し、外部対象は同じ対象を維持する。
- 移設後の実試験でMSBuildが全体.sln外の子Projectへ構成を引き継がない問題を検出。ローカルVS2022のMicrosoft.Common.CurrentVersion.targetsにあるShouldUnsetParentConfigurationAndPlatformの既定動作を確認し、APIの全体ビルドではfalseを指定して修正。外部CMakeソースを含むDebug/Releaseの全体・個別ビルドで回帰確認する。
