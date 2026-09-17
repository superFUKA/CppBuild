# 更新・ビルド設定・生成物の詳細案

> 2026-09-17 実装開始後の追記：M1（管理モデル・設定保存・非保存ビルド設定）を実装し、自動テスト13件を確認。最新の進捗・検証・次の作業は[実装記録](IMPLEMENTATION_STATUS.md)を参照。以下の「未着手」「実装はまだ開始しない」は設計整理時点の記録であり、今回の実装依頼を制限しない。設計の合意状態は維持する。

更新日：2026-09-17。各Projectの独立したCMake構成による個別更新は確認済み。**全体への統合方法、設定メソッドの具体名、管理設定の保存形式等は詳細案であり、実装・実ビルド検証は未実施。** 合意済みの責務は[API_DESIGN.md](API_DESIGN.md)、残る判断は[DESIGN_NOTES.md](DESIGN_NOTES.md)。

## 最新の合意：各Projectの独立したCMake構成で個別更新

各Projectに単独で構成・生成できるCMake入口と専用ビルドディレクトリ・キャッシュを持たせ、project.updateはその生成環境だけを再構成・再生成する。対象ソースの一覧とフィルターの反映を主目的とする。この操作がCMakeの標準操作で実現できることを説明し、ユーザーから問題ない旨を確認した。

更新されるのは.vcxproj一つに限定されず、.filters・CMake管理ファイル・個別生成される.sln等も含み得る。個別生成した.vcxprojを一つの全体.slnへまとめる方式、依存関係・構成対応・更新順序は未確定。include_external_msprojectは統合候補として調査し、採用済み・検証済みとは扱わない。

共通ビルドツリー全体の再生成をproject.updateの前提とする直前の推奨は撤回する。以下の共通ツリー案と、全体用に別の.vcxprojを生成する案は比較記録として残す。今回の合意は、いずれかの全体統合方式や重複成果物の管理方法まで確定するものではない。

参考：[CMakeの構成・生成操作](https://cmake.org/cmake/help/latest/manual/cmake.1.html)、[include_external_msproject](https://cmake.org/cmake/help/latest/command/include_external_msproject.html)。

## 比較記録：フィルター反映を共通ビルドツリーで行う案（未採用）

ユーザーの補足により、updateの主目的をソース配置からVSのファイル一覧・フィルターへの反映として明確化した。**以下の別ツリー案は過去の比較候補であり、現在の推奨案ではない。** 以下の独立ツリー前提の配置・サンプル・状態管理・cleanもその候補に属する。設定データについては第3・4節およびSETTINGS_DESIGN.mdの最新方針を参照する。

当時の案は、一つのSolutionの共通ビルドツリーを使い、project.updateで対象Projectだけの管理設定・ソース配置を読み取り、ソース一覧とフィルター定義を差し替えてから共通のCMakeを再実行する方式。solution.updateは全所属Projectを走査する。実ファイルを自動移動する意味ではない。

target_sourcesでtargetの一覧を定義し、Projectごとに分けたCMakeディレクトリでsource_group(TREE ...)を使う。source_groupのスコープはディレクトリであり、そのディレクトリで作成したtargetへ適用される。同名フィルター等の他Projectへの漏れを防ぐ。

通常の個別updateでは、他Projectは直前に認識した明示的なファイル一覧・設定の生成入力を維持する案。全Projectで自動GLOBを使って走査範囲を暗黙に拡大しない。CMakeの構成処理自体は全体へ及ぶ。依存変更・外部ファイル削除・初回生成で一覧がない場合は、必要な追加読み込みを行うか全体updateが必要と診断するかを設計する。無関係な構成エラーから完全に独立できるとは約束しない。

個別update成功で未走査Projectの未反映状態を解消しない。同じツリーを使う場合の未反映状態は、Projectごとの取り込み済みスナップショットと、共通生成の成功状態を区別する。ProjectごとのCMake定義を持つことは、独立したトップレベル入口・.slnを持つことを意味しない。

この方式なら個別用.slnは不要。個別buildは同じツリーの対象targetを指定する方向へ見直す。個別cleanの共有依存保護、初回生成、複数構成の取り込み、既存の独立操作方針からの変更はまだ未合意・未検証。

根拠：[target_sources](https://cmake.org/cmake/help/latest/command/target_sources.html)、[source_group](https://cmake.org/cmake/help/latest/command/source_group.html)。フィルター反映をProject単位に扱うことと、CMake CLIで単一.vcxprojだけを再生成することは別である。

## 1. 比較候補：全体と個別を別の生成範囲にする方式

既存の独立構成を強く解釈した比較候補として、全体と個別に独立した生成入口・ビルドツリーを持つ方式を以下に残す。同じソース・設定から生成定義を作るが、生成済み.vcxprojやオブジェクトファイルは共有しない。

| 操作 | 読み込み・生成対象 | 主に返す生成物 |
| --- | --- | --- |
| solution.update() | 全所属Projectと必要な依存先 | 全体のSolution名.sln、各.vcxprojとフィルター |
| project.update() | 対象Projectと再帰的な依存先 | 個別用Project名.sln、対象と依存の.vcxproj・フィルター |
| project.add_file等の自動更新 | project.updateと同じ | 同上。全体ツリーには未反映状態を残す |

たとえばApp→Math、Toolは独立という構成なら、App.updateで作る個別用.slnにはAppとMathを含み、Toolは含めない。Solution.updateの全体.slnには3つとも含む。全体のビルド対象選択は、IDEに表示する所属一覧と分ける。CMakeの補助プロジェクトが生成される可能性を結果・表示で区別する。

**利用上の判断点**：個別update後に全体.slnも最新にしたい場合はsolution.updateが必要。この仕様はまだ未合意。個別用.slnの生成はユーザーがSolutionを別途作成・保存する操作ではなく、個別操作のための生成物である。

代替案は単一の全体ツリーを共用する方式。個別updateでも全体のCMake構成・生成を行えば全体.slnを更新できる。ただし無関係なProjectの構成エラーも個別更新へ影響し、個別の完全な独立性は保てない。単に.vcxprojだけを手編集してCMakeを迂回する案は採用しない。

一つの論理Projectが静的版・共有版を同時に必要とされる場合は、内部CMake targetを種類別に分け、対応する複数.vcxprojを生成する案。公開Projectと.vcxprojを常に1対1とはしない。HEADER_ONLYの表示方法とCMakeの補助プロジェクトも含めて実検証する。

## 2. CMake入力とディレクトリ

以下は配置例。パス名は提案であり、利用側が手書きするものではない。

```text
Demo/
  .cppbuild/
    solution.json                 # 所属参照・主Project・共有素材等の管理設定
    templates/
    generated/<context>/          # 全体用CMake入口・依存定義
  App/
    .cppbuild/
      project.json                # ソース範囲・種類・依存・詳細設定
      generated/<context>/        # 個別用CMake入口・依存定義
    src/
  Math/
    .cppbuild/project.json
    src/
  Tool/
    .cppbuild/project.json
    src/
  build/
    solution/<context>/Demo.sln
    projects/<project-id>/<context>/App.sln
```

個別入力は対象Project配下に置く一方、生成物は所有範囲を分けた専用build配下に置く。外部Projectも元ソースへビルド生成物を書き込まず、呼び出した生成範囲のツリーに置く案。設定・ソース・生成CMake・ビルド出力を混同しない。

走査では管理用.cppbuildと生成物の所有ディレクトリをソース対象から除外する。対象Project配下にビルド先を指定しても生成物を再帰的に取り込まない。外部ソースのフィルター基準はルートごとに定め、複数ルートの名前衝突は診断する案。

全体入口と個別入口は共通のPython生成処理からCMake target定義を作る。各入口に直接project()を置き、正規化した依存グラフから対象の定義を一度ずつ組み込む。個別のCMake入口同士を無条件に入れ子にしない。生成したtarget定義をadd_subdirectory等で組み込む際は生成範囲専用のbinary_dirを使う。

contextには生成範囲、VSインスタンス、ジェネレーター、アーキテクチャ、ツールセット、SDK／ツールチェーン等の構成条件を反映する。条件の識別情報はマニフェストに保存し、名前やハッシュだけで互換性を判断しない。異なるcontextでCMakeCacheを使い回さない。

ソース一覧等の通常変更では同じcontextを更新できる。生成結果の状態には設定・ファイル一覧の指紋を持ち、構成条件のキーと更新必要性を分ける。ロックは生成入口・ビルドツリーの単位。異なる処理が同じ生成CMakeへ同時書き込みしない。

## 3. ビルド設定のデータと受け渡し

確認済み：Solution／Projectへ構造化したビルド設定をメソッドで渡す。ビルド設定はメモリ上だけに保持し、外部設定ファイルへ保存しない。具体名はset_build_settingsを提案する。コピー保持・全置換・未設定時の挙動は詳細案。

Solution／Projectの管理設定は保存する。Projectが複数種類へ対応する場合は、種類別の管理設定ファイルを持つ。呼び出し時のビルド設定を種類ごとに保存する意味ではない。

親Solutionの共通値へ従う継承指定をProjectに設ける案を追加した。configuration・architecture等の適用可能な項目を、子の明示値または親の共通値へ解決する。独立CMake構成間の継承はライブラリ側で解決してから渡す。

具体的な役割分担・例・合意範囲は[SETTINGS_DESIGN.md](SETTINGS_DESIGN.md)を参照する。

## 4. 保存・読み込みとの関係

旧案の管理設定JSON内のbuild_settings保存、ビルドプロファイル保存、openでの実行用設定復元は撤回する。get/save/reloadはSolution／Projectの管理設定と種類別管理設定を扱う。

set_build_settingsで受け取った値はsaveで書き出さず、reloadで保存値へ切り替えない。updateは管理設定を再読み込みしつつ、メモリ上の実行用設定を保持して整合を検証する案。新しくopenしたインスタンスへは利用側から必要なビルド設定を渡す。

全体の.cppbuild/solution.json、個別の.cppbuild/project.json、個別の.cppbuild/types/<kind>.jsonという配置は案。各設定ファイルを読み込んでも、種類ごとの相対パスはProjectルート基準とする。保存形式の検証、競合、複数ファイル更新の途中失敗は実装時に具体化する。

CMake入力・キャッシュ・VS生成ファイルは処理に必要な値を含む生成物であり、ビルド設定データの保存・復元APIとは区別する。生成物を実行用設定の復元元にしない。

## 5. 依存・構成の解決

全体操作は全体設定の選択集合、個別操作は呼び出し先を起点に依存をたどる。設定の読み込み・循環・存在・種類・構成整合を検証してからCMakeを起動する。依存の識別子はSolution／Projectの安定IDと種類を含む。複数利用側が同じ依存を要求しても同じ生成範囲では一度だけ定義する。

Solution.buildは個別Project.buildの繰り返しにしない。全体ツリーで選択対象に対応するCMake targetを指定し、リンク依存をCMakeに伝える。全体.slnに表示する所属Projectとbuildの選択集合を区別する。VS上の既定ビルド参加とAPI側の選択を揃える生成設定は実検証する。

構成の共通指定は親Solutionからの継承で扱う案。各Projectの明示値を維持しつつ、アーキテクチャ・ランタイム等の依存間整合を検証する。混在の許容範囲は未確定。以下の全体ツリー関連の説明は比較案であり、独立したProjectの統合方式の確定を意味しない。

VSジェネレーターは複数configurationを持てる。Debug／Releaseはビルド時に選択し、切り替えるだけで別キャッシュを作らない。種類や構成に応じた設定はCMakeのtargetプロパティ等へ変換する。成果物パスは固定名を推測せず、File API等の生成情報から対象・構成ごとに得る案。

## 6. 状態・共有依存・clean

生成状態は「生成範囲＋context＋参照する設定・ソースの状態」で保持する。コンパイル結果はさらにconfigurationと種類を区別する。Appのファイル変更はApp個別ツリーと、Appを含む全体ツリーを古い状態にする。依存変更は、その依存を使う生成範囲も無効化する。

project.update成功で全体の未反映フラグを消さない。solution.update成功で別の個別ツリーを最新扱いしない。build前に今回使う生成範囲を走査・検証し、未反映なら更新してからビルドする。外部でのファイル・設定変更もこの時点で検出する。

同じツリー内ではMathやGoogleTestを共有するが、全体と個別、別Projectの個別ツリー間ではコンパイル済み成果物を共有しない案。依存のソース素材を共有する場合でも、バイナリ生成先は分離する。重複ビルド・ディスク使用増がこの案の代償であり、ツリー間の成果物再利用は初期範囲に含めない。

cleanは選択した範囲・configuration・Projectの所有成果物だけを扱い、ソース・保存設定・他の生成範囲を削除しない。共有依存は参照されている限り残す。全体cleanも対象集合を計算し、他の所属Projectが必要とする依存を保護する。

`cmake --build ... --target clean`を個別cleanへ無条件に使わない。CMake/MSBuildのCleanが依存成果物まで消すかを検証し、必要なら生成時に把握した所有成果物を使う専用clean処理を設計する。安全な所有一覧が作れないケースを成功扱いしない。rebuildは同じ所有範囲のclean後にbuildする案。

出力先の任意指定でも、全体／個別の生成範囲が同じ成果物を上書きしないよう検証する。ユーザー指定出力先を無条件に丸ごと削除しない。キャッシュ不整合をディレクトリ削除で隠さず、別contextを作るか診断する。

## 7. 結果と失敗時の扱い

UpdateReportには要求対象、実際に生成したProject・依存、生成範囲、ビルドディレクトリ、.sln/.vcxproj/.filtersのパス、診断、未反映の別範囲を含める案。ビルド結果にはconfiguration・種類と成果物を含める。

事前検証失敗ならCMakeを起動しない。CMake生成失敗ならその範囲を最新にしない。CMakeが一部ファイルを書いた可能性があるため、自動的な完全ロールバックを約束せず、ビルドへ進まない。ファイル操作が完了していればFileOperationReportに残す。

全体生成は独立した生成工程一つとして結果を返す。個別ツリーを裏で全部再生成しない。イベントで複数Projectへ変更した場合も、それぞれの更新結果と全体未反映状態を区別する。

## 8. 公式機能の確認と未実施の検証

公式資料から確認したのは以下の機能であり、本案の組み合わせが動くことはまだ未検証。

- [VS2022 generator](https://cmake.org/cmake/help/latest/generator/Visual%20Studio%2017%202022.html)：VS2022向け生成、アーキテクチャ・ツールセットの指定。
- [project](https://cmake.org/cmake/help/latest/command/project.html)、[add_subdirectory](https://cmake.org/cmake/help/latest/command/add_subdirectory.html)：トップレベル入口と下位定義、binary_dir指定。
- [cmake CLI](https://cmake.org/cmake/help/latest/manual/cmake.1.html)：source/build treeとキャッシュ、生成とビルドの分離。
- [複数configuration](https://cmake.org/cmake/help/latest/prop_gbl/GENERATOR_IS_MULTI_CONFIG.html)：VSではCMAKE_CONFIGURATION_TYPESを使いCMAKE_BUILD_TYPEを使わない。
- [source_group](https://cmake.org/cmake/help/latest/command/source_group.html)：ソース配置からのIDE表示グループ。
- [File API](https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html)：生成されたビルドシステム情報を取得する仕組み。

実装後の最小検証は、App→Mathと独立Toolによる全体／個別生成、ビルド対象選択、ファイル追加のフィルター反映、Debug／Release切替、設定save/open、失敗時の状態、個別cleanの非干渉。静的／共有同時生成・HEADER_ONLY表示・GoogleTest共有も別途検証する。

## 最新追加事項：実行順とGoogleTest導入

実行順は既定を用意し設定で変更できることを確認済み。既定の具体案と、CMakeのFetchContent・GoogleTestモジュール・CTestによる導入案は[SETTINGS_DESIGN.md](SETTINGS_DESIGN.md)を参照する。実際の取得・ビルド・テストは未実施。
