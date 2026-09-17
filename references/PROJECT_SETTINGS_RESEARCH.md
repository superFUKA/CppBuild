# PCH以外の設定候補

調査日：2026-09-16。公式資料による軽い調査。プロジェクト設定の実装時に参照する検討メモ。実装や実ビルドは行っていない。

ユーザーの意向：特にC++規格と成果物の出力先が必要そうなので、設定クラスの実装時に設定データの項目として考慮する。項目ごとに専用APIを定義・確認する話ではなく、既存のsettingsの取得・保存の仕組みで扱う。フィールド・型・配置・既定値・検証は設定クラスの実装時に整理する。以下の調査候補全体を採用済みとはしない。現行設計は[API_DESIGN.md](../deliverables/API_DESIGN.md)のジャンル3を参照。

## 優先して検討する設定

| 項目 | 用途・提案 | 公式資料 |
| --- | --- | --- |
| C++規格と拡張の許可 | 既出の規格指定に、要求規格を満たせない場合のエラーとコンパイラー独自拡張のON/OFFを含める。公開ヘッダーの要求規格の伝播は別途整理する。 | [CXX_STANDARD](https://cmake.org/cmake/help/latest/prop_tgt/CXX_STANDARD.html) |
| 警告設定 | 警告レベルと警告をエラーにする設定。レベルはコンパイラーごとの対応付けが必要。外部依存に自作コードの厳しい警告方針を無条件に適用しない案。 | [COMPILE_WARNING_AS_ERROR](https://cmake.org/cmake/help/v4.2/prop_tgt/COMPILE_WARNING_AS_ERROR.html) |
| MSVCランタイム | /MD・/MTとDebug用の選択。ターゲット種類の静的・共有とは別項目。GoogleTest等の依存との整合を検証する設計が必要。CMake側はCMP0091をprojectより前に適切に設定する。 | [MSVC_RUNTIME_LIBRARY](https://cmake.org/cmake/help/latest/prop_tgt/MSVC_RUNTIME_LIBRARY.html) |
| ライブラリの公開シンボル | DLLの公開関数・クラスに使うマクロのヘッダー生成。静的・共有を同じソースで扱う方針と相性がよい。公開宣言へのマクロ付与はソース側で必要で、設定だけで任意のコードを共有化できるとはしない。 | [GenerateExportHeader](https://cmake.org/cmake/help/latest/module/GenerateExportHeader.html) |
| 成果物の出力先 | 実行ファイル等の出力先を指定。Debug/Releaseや静的・共有の衝突を防ぐ配置を設計する。ライブラリ等は対応する別の出力プロパティも必要。 | [RUNTIME_OUTPUT_DIRECTORY](https://cmake.org/cmake/help/latest/prop_tgt/RUNTIME_OUTPUT_DIRECTORY.html) |

## 必要に応じた設定

- PIC：位置独立コードを生成する設定。共有ライブラリへ組み込む静的ライブラリなどで検討。SHARED/MODULEではCMakeの既定が有効。[POSITION_INDEPENDENT_CODE](https://cmake.org/cmake/help/latest/prop_tgt/POSITION_INDEPENDENT_CODE.html)
- IPO/LTO：複数ファイルをまたぐ最適化。対応確認を行い、主にRelease向けの任意設定とする案。[INTERPROCEDURAL_OPTIMIZATION](https://cmake.org/cmake/help/latest/prop_tgt/INTERPROCEDURAL_OPTIMIZATION.html)

## 設定の配置案

実際にコンパイルへ作用する項目はtarget.settingsに置き、共通の既定値をproject.settingsに置く構成を提案する。優先順位・設定解除時の継承復帰・保存値と有効値の区別は未決定。ビルド構成やプラットフォームによって異なる値も扱う必要がある。

インクルードパス、マクロ定義、コンパイルオプション、Debug/Release等のビルド構成は既出項目として具体化を続ける。今回の候補は設定クラス実装時の参考とし、設定項目ごとの専用メソッドの追加やAPI確認は行わない。
