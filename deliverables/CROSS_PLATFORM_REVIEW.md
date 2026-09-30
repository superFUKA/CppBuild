# Windows依存の調査と移植方針案

次の担当者は [対応計画・API案](CROSS_PLATFORM_PLAN.md) を先に参照。本資料は依存箇所の調査記録。ユーザーの希望と提案段階の細部は対応計画で区別している。

調査日：2026-09-30。対象：`ae714f1`。静的調査と公式資料の確認結果。以下の変更案は未実装・未合意であり、Linux/macOSでの動作確認は未実施。

## 結論

現在はVS2022の画面を開かなくても、通常のupdate/build/clean/runがWindows・VS2022を前提とする。管理データや依存グラフは再利用できるが、生成器の変更だけでは移植できない。

ユーザーの希望は、VS2022で開く用途以外の通常操作をWindows限定にしないこと。通常の管理・ビルドと、VS向けの.sln/.vcxproj生成を分離する方針を推奨する。現行のVS生成方式自体がWindows側のツールを必要とするため、GUI起動の有無だけで制限を分けることはできない。

なお、現在の `Solution.open()` は管理設定の読み込みであり、VSアプリケーションの起動ではない。

## 実装上の依存

行番号は調査時点。

| 箇所 | 現状と影響 | 必要な対応 |
| --- | --- | --- |
| `cppbuild/engine.py:261` | 個別生成は `Visual Studio 17 2022` と `-A` 固定 | 生成器とコンパイラー環境を選択可能にする。`-A` は対応する生成器だけへ渡す |
| `cppbuild/solution_engine.py:38–95` | 子の.vcxprojからGUIDを読み、`include_external_msproject` とMSBuildで全体を構築 | 通常の全体ビルドを依存グラフの実行に分離し、.sln統合はVS専用処理にする |
| `cppbuild/engine.py:115`、`solution_engine.py:45` | ビルド場所が `vs2022-...` 前提 | 生成器・ツールチェーン・対象環境を区別したキャッシュと所有情報へ変更 |
| `cppbuild/core.py:43`、`models.py` | アーキテクチャはx64/Win32/ARM64、既定x64。生成器の選択肢なし | 通常はホスト標準を利用し、VSのプラットフォーム名と共通設定を分離 |
| `cppbuild/engine.py:180–189` | 管理Project間のリンクで静的.lib、共有.dllと.libを探索 | CMakeが報告する成果物とリンク用ファイルの役割を利用する |
| `cppbuild/dependencies.py:138–145`、`environment.py` | 外部共有ライブラリにもインポートライブラリを一律要求 | 対象OS・ツールチェーンに応じて必要性を判定する |
| `cppbuild/engine.py:347,399` | 実行ファイルを.exeで選別 | 実行可能ターゲットの成果物を特定する。Unixの拡張子なし実行ファイルにも対応 |
| `cppbuild/engine.py:386–396` | .dllの所在をPATHへ追加 | WindowsのDLL探索とLinux/macOSの共有ライブラリ探索を分け、RPATH等を検証 |
| `cppbuild/engine.py:355–373` | cleanは.vcxprojを要求し、MSBuild専用オプションを渡す | 生成器別の削除処理を用意し、所有範囲・構成・依存先の保護を維持 |
| `cppbuild/environment.py:72–101` | Windows以外のコンパイラー検査を失敗にし、VSで試験ビルド | 選択された生成器・コンパイラーで検査する |
| `cppbuild/core.py:546–548`、`relocation.py:111–112,175` | ファイル・Project移動は非Windowsで拒否。Windowsの上書きしないrenameに依存 | 競合時にも既存宛先を上書きしない移動処理をOS別に実装 |
| `cppbuild/templates.py` | ステージング後のrenameも移植時に確認が必要 | 宛先出現競合、失敗時の復元、権限・シンボリックリンクを試験 |
| `tests/test_vs2022.py`、`test_project_types.py`等 | 実ビルド試験がVS環境変数、Windowsの拡張子、`__declspec`等を前提 | 共通試験とVS専用試験を分け、各OSで実ビルドする |

`pathlib`、JSON保存、GUID解決、依存順序、Pythonのプロセス実行、パッケージ構成には再利用できる部分が多い。ただし、それだけで非Windows動作を保証するものではない。Windowsのドライブ表記を検出する処理や環境変数名の大小文字処理は、必要なOS別対応であり、一括削除しない。

## 推奨する構成

### 通常のビルド

最初の対応候補は **Ninja Multi-Config**。現行の複数構成と `--config` を使う方式を維持しやすい。Ninjaの追加導入が必要になる点は明示する。単一構成のNinja/Makefilesまで最初から対応する場合は、構成ごとの生成場所と依存先成果物の扱いも変更する必要がある。

生成器・コンパイラー・ツールチェーンの選択は、既存方針どおり保存しないビルド設定で受け取る案とする。ホストOSと生成対象OSは区別して設計するが、初回の検証範囲は各OSのネイティブビルドとし、クロスコンパイル対応を約束しない。WindowsでNinjaを使う場合にも、選択したコンパイラーの環境設定は必要。

各Projectの独立したCMake構成を維持し、全体ビルドはPython側で選択Projectとその依存先を順番に実行する。共有依存を重複してビルドしない。同じ生成器・設定なら全体／個別の成果物を共用する。VSとNinjaのキャッシュは分離し、生成器をまたぐ成果物共用は前提にしない。

VS画面の表示用に列挙する未参照Projectと、通常ビルドに必要なProjectの集合も分離する。

### VS2022との連携

既存の.sln階層、外部Project表示、GUIDによる統合はVS専用処理として残せる。通常の `update()` を.sln生成必須にせず、明示的にVS向け生成を選ぶ入口を設ける案が自然。具体的な公開API名と既存動作からの移行方法は実装前に整理する。

UpdateReportも.sln/.vcxproj/.filtersの存在を共通の成功条件にせず、生成器共通の結果と任意のIDEファイルを区別する必要がある。

### リンク・実行・clean・移動

- `.a/.so/.dylib` を条件分岐に追加するだけで済ませず、実行用・リンク用成果物をCMakeの情報から取得する。WindowsでもMSVCとMinGWで拡張子が異なる。既存のFile API読取処理を拡張できるが、インポートライブラリ等の情報が不足する場合はCMakeから追加出力する。
- 外部のビルド済みバイナリやユーザーのC++コード自体がOS固有の場合は、そのまま他OSで利用できない。相対パス保存は配置の移設を助けるが、バイナリ互換性は提供しない。
- cleanは「削除前にconfigureしない」「未選択Projectが使う依存先を保護する」を維持する。汎用の `--target clean` への単純置換は採用しない。外部CMakeソースやGoogleTestを同じツリーに生成する場合も含め、生成器別の削除範囲を実検証する。File APIの成果物一覧だけでは全中間ファイルを列挙できるとは限らない。
- POSIXのrenameは既存ファイルを置換し得る。移動前の存在確認だけでは競合を防げないため、上書きしない操作と失敗復元を用意する。ファイル／ディレクトリ、別ファイルシステム、宛先競合を分けて検証する。

## 次の実装・検証の順序案

1. 共通ビルド設定、生成器別処理、キャッシュ識別、レポートとVS生成の入口を設計する。
2. Ninja Multi-Configで個別update/build/run/testと、成果物・共有ライブラリの処理を実装する。
3. 全体の依存順実行、選択ビルド、個別／全体のcleanを実装する。既存の所有・保護規則を回帰試験する。
4. 移動・テンプレート・パス・保存の非Windows対応を実装する。
5. Windows/MSVC、Linux/GCCまたはClang、macOS/AppleClangで実ビルドし、通常操作とVS専用操作の対応表を更新する。

試験には静的／共有／インターフェースの切り替え、外部Solution・GUID優先、外部CMake・ビルド済みライブラリ、Debug/Release、共有ライブラリを使う実行とテスト、部分clean、宛先競合、移設を含める。Windows上のNinja成功だけではLinux/macOS対応済みとしない。

## 今回の検証範囲

ソース・設定・試験・資料の静的調査と公式仕様の確認。実装変更、ツール導入、テストの再実行は行っていない。調査環境はWindowsで、PATH上ではCMakeを確認、Ninja・g++・clang++は検出されなかった。PATH外のインストール状況やWSL内の環境は未確認。

## 2026-09-30 追補：コード照合と環境確認

表の行番号は `ae714f1` の現行コードと一致することを確認した。表にない依存・影響箇所：

- `cppbuild/graph.py` の依存解決と `solution_engine._prepare`、`environment.check_owner` が configuration/architecture/tools の一致を判定する。生成環境の一致判定もここへ加える必要がある。
- `cppbuild-owner.json` の所有情報は `architecture` を含み、生成器を含まない（`engine.py:247,360`）。
- 全体ビルドは `CMAKE_VS_MSBUILD_COMMAND` を使う（`solution_engine.py:77`）。
- `templates.py:187` のステージング後の `rename` は、POSIXでは宛先が空ディレクトリなら置き換えて成功する。`move_file` も非Windowsでは拒否している（`core.py:547`）。
- 公開値の `EnvironmentReport.architecture` と診断項目名 `vs2022` も互換性の対象になる。

環境：Visual Studio 2022（17.14）と2026（18.10）が導入済み。CMake 4.2.3 の既定生成器は `Visual Studio 18 2026` なので、CMakeの既定に任せると現行と異なる生成器になる。Ninja はPATH上にないが、VS付属のものがある。WSLは未導入のため、この環境ではLinux/macOSを検証できない。

スクラッチ領域での事前確認（MSVC＋VS付属Ninja、Ninja Multi-Config）：
- 共有ライブラリと実行ファイルの構成・ビルドに成功した。
- `file(GENERATE)` の `$<TARGET_FILE>` / `$<TARGET_LINKER_FILE>` で、.dll・インポート.lib・.exeを役割付きで取得できた。File APIのartifactsには.pdbも含まれ、役割の区別はない。
- `cmake --build --config Debug --target clean` はDebugの成果物だけを削除し、Releaseを残した。CMakeLists.txtを変更した後でも、`CMAKE_SUPPRESS_REGENERATION` により再構成は起きなかった。
- .pdb・.ilk・.expは削除されずに残った。

これは設計判断用の確認であり、本ライブラリでの実装・検証ではない。

## 参照した公式資料

- [CMake: Ninja Multi-Config](https://cmake.org/cmake/help/latest/generator/Ninja%20Multi-Config.html)：複数構成と `--config`。
- [CMake: include_external_msproject](https://cmake.org/cmake/help/latest/command/include_external_msproject.html)：VS以外の生成器では無視される。
- [CMake: File API](https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html)：ターゲット種別・成果物情報。
- [CMake: IMPORTED_IMPLIB](https://cmake.org/cmake/help/latest/prop_tgt/IMPORTED_IMPLIB.html)：対象プラットフォームごとのリンク用ファイル。
- [Python: Path.rename](https://docs.python.org/3/library/pathlib.html#pathlib.Path.rename)：UnixとWindowsで異なる既存宛先の扱い。
