# 設定の受け渡し・継承・種類別設定・実行順

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

共通値と種類固有値を分け、参照された種類ファイルを自動で読み込む。型とファイルの対応、重複・不足・非対応種類を検証する。分割しても相対パスの基準はProjectルートを維持する。

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

## 2026-09-30追加：GUIDと名前付き参照の保存

- Project管理ファイルにUUID形式の `guid` を保存する。新規作成で発行し、通常saveでの変更は禁止。Project移動で保持し、テンプレート作成・復元では所属Projectごとに新規発行して内部依存を付け替える。
- Solution管理ファイルの `references` に参照名と対象GUID・外部Solutionの管理ディレクトリを保存する。新規リンク時の所在は絶対パスへ正規化。Projectの外部 `Dependency` は `reference` に登録名を保持し、`solution_directory` / `project_guid` はNoneとする。`project` は登録時の名前、`project_type` は使用する種類。内部Dependencyは `project_guid` を保持する。
- 参照の利用数を別途保存せず、所属Projectの依存一覧から算出する。最後の依存の解除・Project登録解除で参照名を自動削除する。別名はそれぞれ独立して整理する。
- 依存保存時はSolutionと全所属Projectのロック・設定指紋を確認し、変更したProjectとSolutionの文書をまとめて公開する。通常の書き込み失敗では公開済み文書を復元し、メモリ上の状態は成功後に更新する。強制終了・停電をまたぐ自動復旧は対象外。
- schema_version=1の旧設定はopen/reload時に自動移行する。GUID未保存のProjectにGUIDを付与し、外部パスをSolutionの一覧へ集約。既存dependency_idを保持する。外部リンク先のGUID付与はリンク先Solution単位で行い、利用側の移行が失敗しても完了済みのリンク先GUIDは保持する。移行後の再読み込みでは書き込まない。
- コピーで同じGUIDが別所在に現れた場合は、参照登録または依存解決で衝突として拒否する。外部Solution全体の移動先の自動探索・専用の所在更新APIは追加しない。全利用元でunlink後、新しいパスへ再リンクする。外部Solution内のProject移動はGUIDで追跡する。
