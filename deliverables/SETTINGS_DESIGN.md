# 設定の受け渡し・継承・種類別設定・実行順

## 2026-10-01追加：出力先の非保存設定

- ProjectBuildSettingsに`intermediate_directory="output/intermediate"`と`artifact_directory="output/artifacts"`を追加。SolutionBuildSettingsは`intermediate_directory="output/intermediate"`だけを持つ。
- 出力先の継承・Solutionによる所属Projectの一括設定は設けない。INHERITとNoneは不可。既存のconfiguration・architecture・cpp_standard・tools・cmakeの継承は維持する。
- 相対パスは設定対象自身の`.cppbuild`基準。`..`と絶対パスを受け付け、ドライブ相対パス・空文字・CMakeで文字列として扱えない値は拒否する。
- 管理JSON・スキーマは変更しない。再open時に出力先設定を復元しない。生成物の所有マーカーと成果物領域の記録は上書き・cleanの照合用で、設定復元元にはしない。
- 外部Solutionは従来どおり依存操作内で再openする。別インスタンスのProjectBuildSettingsは伝播せず、外部Projectの出力先は既定値。外部Project単位の設定を操作へ渡すAPIは今回追加しない。

> 2026-09-17 実装開始後の追記：M1（管理モデル・設定保存・非保存ビルド設定）を実装し、自動テスト13件を確認。最新の進捗・検証・次の作業は[実装記録](IMPLEMENTATION_STATUS.md)を参照。以下の「未着手」「実装はまだ開始しない」は設計整理時点の記録であり、今回の実装依頼を制限しない。設計の合意状態は維持する。

更新日：2026-09-17。メソッドでのビルド設定受け取り、ビルド設定非保存、管理設定保存、種類別ファイル、変更可能な実行順は確認済み。最新のユーザー指示により、以下の継承規則・既定の実行方式・GoogleTestのFetchContent導入も現時点の案で暫定採用とする。後から変更可能な実装上の細部は、その都度の承認を必須にせず具体化する。実装・実ビルド検証は未実施。

## 1. 保存する管理設定と、保存しないビルド設定

| 区分 | 役割 | 保存 |
| --- | --- | --- |
| SolutionSettingsData | 所属Project、main_project、共有素材、管理上の共通設定等 | 保存・再読み込みする |
| ProjectSettingsData | 名前、ソース範囲、対応種類、依存登録、PCH、種類別設定への参照等 | 保存・再読み込みする |
| 種類別Project設定 | 静的版・共有版等、それぞれの構成定義 | 種類ごとのファイルに保存する |
| SolutionBuildSettings | 今回オブジェクトへ渡す全体の対象選択と、個別へ継承させる実行時の共通値 | メモリ上だけ。専用設定ファイルへ保存しない |
| ProjectBuildSettings | 今回オブジェクトへ渡す個別の構成・生成種類等 | メモリ上だけ。専用設定ファイルへ保存しない |

種類別ファイルはProject管理設定の分割であり、ProjectBuildSettingsの保存ではないと整理する。種類ごとの定義（例：共有版固有の公開マクロ・リンク条件）と、呼び出し時の選択（例：Release、x64、共有版を生成）は分ける。C++規格・出力先等をどちらのデータに置くかは項目設計時に確定し、同じ値を無意識に二重管理しない。

旧案の「管理設定JSON内にbuild_settingsを保存」「saveで実行用設定を永続化」「open時に保存したビルド設定を復元」「名前付きビルドプロファイルを保存」は撤回する。利用側が必要なデータを作り、必要なオブジェクトへ渡す。

生成したCMake入力、CMakeCache、.vcxprojには処理に必要な値が反映される。これらは再生成可能なビルド生成物であり、Solution.openで実行用設定を復元する保存先にはしない。ユーザー管理のビルド設定JSONやビルドプロファイルは作らない。

## 2. メソッドによる設定

メソッドで渡すことは確認済み。具体名は以下を案とする。

```python
solution.set_build_settings(solution_build_settings)
project.set_build_settings(project_build_settings)

solution.build()
project.build()
```

検証した独立コピーを保持し、再呼び出し時に全置換する案。操作開始時に親と子の設定をまとめてスナップショット化する。操作途中の設定変更は次の操作から反映する。コピー取得・全置換・未指定時の値の扱いは詳細設計であり、メソッドという入口の合意と区別する。

settings.get/save/reloadは保存対象の管理設定だけを扱う。保存・再読込でメモリ上のビルド設定を保存・復元・消去しない。updateは管理設定を再読込しても、メソッドで受け取ったビルド設定を保持し、新しい管理設定と整合を検証する案。

Solution.openは管理設定と所属Projectを読む。必要な実行用設定は利用側から改めて渡す。型の明示的な既定値で満たせない必須値は操作前に診断し、古いCMakeCacheから推測して復元しない。未設定／設定解除のAPIは項目設計と合わせて決める。

2026-09-28実装：`solution.move_project`は全体project.jsonのprojectsと、必要な個別・種類別設定のパスを保存し直す。移動対象のProject/Settingsオブジェクトを保持するため、非保存ビルド設定は初期化しない。移動対象内部の参照は新位置へ、外部相対参照は同じ参照先を維持するよう補正し、同一Solutionの他Projectに保存された型付きパスも対象とする。非保存のGoogleTest ZIPと明示ツールパスも補正するがJSONには保存しない。任意のrun_arguments・コンパイル定義・環境変数やファイル本文に埋め込まれたパスは解析しない。

## 3. Projectから親Solutionへの継承（暫定採用）

ユーザーの「親に従う」案は、全体構成と個別構成の指定を揃えるために有効。親は所属Solutionとし、依存先Projectや呼び出し時の別Solutionを親にしない。独立CMake構成間の継承なので、ライブラリが解決した値を個別CMakeへ渡す。

SolutionBuildSettingsへ共通値の領域を設け、Project側は項目ごとにINHERITを指定できる案。ソース範囲・対応種類・リンク登録・ビルド対象一覧など、役割の異なる項目まで無条件に継承しない。INHERITという綴りは未確定。

| 親の共通値 | 子の値 | 解決結果 |
| --- | --- | --- |
| configuration=Release | INHERIT | Release |
| configuration=Release | Debug | Debugを維持 |
| architecture=x64 | INHERIT | x64 |
| 必要な値なし | INHERIT | 操作前に診断 |

継承可能な項目の優先順位は、子の明示値 → 親の共通値（INHERIT指定時）とする。空一覧・空文字を継承扱いにしない。種類別の管理設定は構成の定義であり、親から常に上書きする対象にしない。

継承方針をProject管理設定に保存する形も候補。その場合も親の実行用ビルド値そのものは保存しない。継承指定を管理設定に置くかProjectBuildSettingsに置くか、項目ごと／一括の入口は未確定。

個別project.buildでも所属Solutionのメモリ設定を使って継承を解決する案。Solutionの対象一覧に含まれるかとは無関係。親の変更に追随するProjectの生成状態を古くし、次の個別操作で必要なら再生成する。

この仕組みは設定の重複指定を減らすが、異なるアーキテクチャのリンク等の不整合を自動解決するものではない。子の明示値で依存間の構成が食い違う場合は整合検証する。構成混在を無条件に許可する合意にはしない。

## 4. 種類ごとの管理設定ファイル

複数種類に対応する場合は種類別ファイルを持つ方針を確認済み。以下の名称・配置は案。

```text
Library/.cppbuild/
  project.json                 # 共通管理設定と種類ファイル参照
  types/
    static_library.json       # 静的版の定義
    shared_library.json       # 共有版の定義
```

共通値と種類固有値を分け、参照された種類ファイルを自動で読み込む。型とファイルの対応、重複・不足・非対応種類を検証する。APIでの相対パスはProjectルート基準を維持する。schema_version=2の保存JSONは種類ファイル自身の親ディレクトリ基準へ変換する。

利用側がファイルの読み書きを個別に行う必要はなく、project.settingsが保存・読み込みを担当する。複数ファイル保存の途中失敗・外部編集との競合は実装時に設計する。独立した種類用の公開管理クラスを必須にはしない。

種類別ファイルは対応構成を定義するもので、今回どの種類をビルドするかはProjectBuildSettingsで選ぶ。同時生成の指定形式・既定選択・許容組合せは引き続き設計事項。

## 5. 実行順の既定と設定（暫定採用）

2026-09-18：M5の具体フィールド・既定値・非同期結果は[API設計のM5実装補足](API_DESIGN.md#tests)を参照。すべて実行用設定であり管理JSONへ保存しない。GoogleTestは1.14.0の固定ZIPを採用し、ローカルZIP指定も同じSHA256検証を行う。

既定の動作を用意し設定で変更できることは確認済み。複数アプリの起動順にCMake共通の唯一の既定はないため、以下を本ライブラリの案とする。

| 操作 | 既定案 | 変更できる項目の案 |
| --- | --- | --- |
| build | 依存先を先に処理。依存のないものの並列化はビルドツールへ委ねる | 並列数。明示的な順序制約は依存順を壊さない範囲で扱う |
| run | Solutionで指定した実行対象の並び順に一つずつ起動し、終了を待つ | 対象順、並列起動、終了待機、失敗後の継続 |
| test | 必要なビルド後に実行。初期案ではProject間は指定順、各CTest実行は-j 1を明示 | 並列数、Projectの順、失敗後の継続、必要なテスト依存 |

runの既定は失敗時に後続を止め、終了コードを結果へ残す。testの既定は独立したテスト失敗を収集して全体結果を返す。ビルド失敗した対象のrun/testは行わない。これらは現時点の既定として暫定採用し、実装・検証に応じて見直せるものとする。

runの順序をリンク依存だけから推測しない。CTest内部のケース順はテスト列挙順と同一であると保証せず、必要ならテスト依存・fixture等を使う。独立CMake構成間のビルド依存はライブラリ／全体Solution側で伝達する必要があり、個別CMakeが自動的に別構成の依存を解決すると仮定しない。

## 6. GoogleTestの導入（CMake標準機能による導入を暫定採用）

2026-09-18実装・検証補足：1.14.0 ZIPの固定SHA256でオンライン取得とローカルZIPの両方を検証した。Projectごとの所有ツリー内で取得・ビルドし、Project間のダウンロードキャッシュ共有は行わない。取得はconfigure時、コンパイルはbuild時、ケース列挙はPRE_TEST。利用手順は[USAGE.md](USAGE.md)を参照。

M6の保存境界：共有素材の登録名とSolution内相対パスは`SolutionSettingsData.file_templates`に保存する。旧JSONにこの項目がなければ空辞書として読む。`ToolSettings`はSolution/Projectの非保存ビルド設定に含め、Project既定はINHERIT。環境変数・ツールパス・イベント登録・infoの観測状態を管理JSONやSolutionテンプレートから復元しない。

GoogleTestはCMakeのFetchContentによる取得・組み込み、GTest::gtest_mainへのリンク、gtest_discover_testsでのCTest登録、CTestによる実行という公式例に沿って導入できる。Python側に独自のダウンロード・ビルド処理を作る必要はない。

TEST種類のProject作成時にこのCMake定義をライブラリが生成する案。取得する版は固定し、ソースの取得とバイナリのビルドを区別する。FetchContentは構成時に取得・展開し得るので、初回updateに通信が発生する可能性がある。updateではGoogleTestやテスト本体のコンパイルを行わない。

gtest_discover_testsはビルド済みテスト実行ファイルを使う。updateだけで常にケース一覧が得られるとはしない。test時は必要なビルド後にCTestを実行する。VSのconfiguration選択はビルドとCTestで揃える。

取得方法は固定版FetchContentを暫定採用する。具体的な版・保存配置・複数独立Project間のソース再利用は実装時に選定・検証する。既存パッケージやローカルソースの指定は設計候補として残す。WindowsのランタイムはProjectとGoogleTestで整合させ、公式例のCRT指定を全構成へ無条件に流用しない。

根拠：[GoogleTest公式CMake導入例](https://google.github.io/googletest/quickstart-cmake.html)、[CMake GoogleTestモジュール](https://cmake.org/cmake/help/latest/module/GoogleTest.html)、[FetchContent](https://cmake.org/cmake/help/latest/module/FetchContent.html)、[CTest](https://cmake.org/cmake/help/latest/manual/ctest.1.html)。導入・ビルド・実行の実検証はまだ行っていない。
## 2026-09-29追加：保存する表示設定

SolutionSettingsDataに `solution_folders` を追加。`None` または `SolutionFolderSettings`（`projects`、`linked_projects`、`project_folders`）を保存する。旧管理ファイルで項目がない場合は `None` として読む。ビルド設定には含めない。設定のコピー分離・検証・競合検出は既存のget/saveに従う。テンプレートに含め、登録解除時にそのProjectの配置指定を除く。

## 2026-09-30更新：GUIDによる参照の保存（schema_version=3）

- Project管理ファイルにUUID形式の `guid` を保存する。新規作成で発行し、通常saveでの変更は禁止。Project移動で保持し、テンプレート作成・復元では所属Projectごとに新規発行して内部依存を付け替える。
- Solution管理ファイルの `references` は対象GUIDをキーに、`ProjectReference(project_guid, solution_directory)` を保存する。キーと値のGUIDは一致させる。所在はAPI上でSolutionルート相対、保存JSONでは設定ファイル相対へ正規化する。Projectの内部・外部 `Dependency` は `project_guid` と `project_type` だけを保存し、対象名・参照名・所在は保持しない。
- 参照の利用数を別途保存せず、所属Projectの依存一覧から算出する。最後の依存の解除・Project登録解除で対象GUIDの登録を自動削除する。同じGUIDは全利用元で一つの登録を共用する。
- 依存保存時はSolutionと全所属Projectのロック・設定指紋を確認し、変更したProjectとSolutionの文書をまとめて公開する。通常の書き込み失敗では公開済み文書を復元し、メモリ上の状態は成功後に更新する。強制終了・停電をまたぐ自動復旧は対象外。
- schema_version=1/2はopen/reload時に3へ自動移行する。GUID未保存のProjectにGUIDを付与し、外部パスをSolutionの一覧へ集約。名前付き参照は保存済みGUIDへ統合する。同じ対象への複数の旧別名も一つの登録になる。既存dependency_idをすべて保持するため、同じGUID・種類の依存が複数残る場合も、全解除まで登録を保持する。
- 名前付き参照に対象GUIDが保存済みなら、参照先がオフラインでも名前の移行は可能。GUIDが未保存の旧外部リンクには参照先へのアクセスが必要。循環する旧リンクのGUID付与はリンク移行と分離し、未解決の旧リンクを持つ文書は一時的にバージョン2のまま保持する。利用側の移行が失敗しても完了済みのリンク先GUID付与は保持する。移行後の再読み込みでは書き込まない。
- バージョン3は参照一覧のGUID不一致と依存内の旧名前フィールドを拒否する。GUIDをローカルProjectと外部対象に重複登録することも拒否する。
- コピーで同じGUIDが別所在に現れた場合は、参照登録または依存解決で衝突として拒否する。ただし依存解決では、操作を始めた最上位Solution（Solution操作ではそのSolution、Project操作では所属Solution）の参照一覧に登録されたGUIDは、入れ子の依存先が自分の参照一覧で別の所在に登録していても、最上位の所在を優先して使用する。保存された各Solutionの参照一覧は変更しない（2026-09-30追加）。外部Solution全体の移動先の自動探索・専用の所在更新APIは追加しない。相対配置が変わる場合は全利用元でunlink後、新しいパスへ再リンクする。利用側と外部Solutionの相対配置を保った一括移設は設定変更不要。外部Solution内のProject移動はGUIDで追跡する。

## 2026-09-30追加：相対パスの保存形式（schema_version=2以降）

パス文字列は `/` 区切りで、値を保存するJSONの親ディレクトリからの相対パスとする。APIの入力基準は変更しない。保存JSONを読み込んでgetで返す型付きパスはSolution／Projectルート相対で、絶対パスを入力した場合もJSONでは相対化する。

| 保存先 | 項目と例 |
| --- | --- |
| Solutionの `.cppbuild/project.json` | projects=`../App`、file_templates=`templates/Header.hpp`、referencesの所在=`../../Provider/.cppbuild` |
| Projectの `.cppbuild/project.json` | source_directories=`../src`、project_headers=`../include/pch.hpp`、types=`types/<種類とハッシュ>.json`、外部ライブラリ等も同ファイル基準 |
| Projectの `.cppbuild/types/<種類とハッシュ>.json` | include_directories=`../../include` |

schema_version=1は従来の基準で読み、型付き絶対パスを相対化して既存の文書公開・失敗復元処理で移行する。GUID・dependency_idは保持し、旧リンク名はGUIDへ変換する。移行済みの再読み込みは保存しない。種類別ファイルは新しい内容のファイルを先に作り、Projectの参照を切り替える。旧種類ファイルの自動削除は行わない。

相対化できない別ドライブ／別共有への参照は拒否し、絶対パスへ暗黙に戻さない。移行は元の配置で実施する。すでに別環境へ移された旧絶対パスから、新しい対応先を推測することはしない。

一時ディレクトリで作るテンプレートも、最終出力先を基準に外部参照を補正してから公開する。コピーする内部ファイルへの参照はコピー先へ、外部参照は同じ外部対象へ向ける。CMake生成物・所有マーカー・キャッシュ、非保存ビルド設定、定義文字列やソース本文は管理パス変換の対象外。

## 2026-09-30更新：インターフェースライブラリと形式の制限（schema_version=4）

- HEADER_ONLY/header_onlyをINTERFACE_LIBRARY/interface_libraryへ改名する。現行APIに旧名の別名は残さない。旧設定の種類キー・種類文書kind・依存のproject_typeはバージョン1〜3の読み込み時に変換し、既存の公開・失敗復元処理で保存する。バージョン4では旧名称を拒否する。パスの基準、GUIDとdependency_idを保持する。
- Project文書にinitial_typeを保存する。作成時にadd_projectのproject_typeから設定する変更不可の管理情報であり、実行時の選択を保存するものではない。ProjectBuildSettings.project_typeは引き続き非保存。省略時はinitial_typeを使う。
- ライブラリのtypesはstatic_library・shared_library・interface_libraryの3件を持つ。作成・移行で不足項目を既定のTypeSettingsDataで補う。別形式の定義やincludeパスを暗黙に引き継がない。executableとtestは各1件のみとし、形式の混在や通常saveによる用途変更を拒否する。
- 旧単一形式ではその形式をinitial_typeとする。旧複数ライブラリではstatic_library→shared_library→interface_libraryの優先順で既存の形式を採用する。旧混在形式は自動で設定を捨てず、読込エラーとして旧版での整理を要求する。旧GUID／名前付き参照の移行はバージョン1〜2に限定し、バージョン3を旧名前付き形式として扱わない。
- 移行済み文書の再読込は書き込まない。旧種類ファイルと旧header_only生成物は保持し、新名称の生成物を別の所有ツリーで生成する。Project移動でinitial_typeとGUIDを保持し、テンプレートではinitial_typeを保持してGUIDを新規発行する。
