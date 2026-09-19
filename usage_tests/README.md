# ライブラリを実際に使うテスト用プロジェクト

`run.py` が公開APIを使って複数のSolutionとC++ソースを作成し、VS2022で生成・コンパイル・リンク・実行・CTestまで行います。モックや既存の単体テストの呼び出しは使いません。ソース・管理設定・Visual Studio生成物・実行ログは削除せず残すため、実行後にプロジェクトを開いて変更できます。

今回の検証結果と生成先は[RESULTS.md](RESULTS.md)を参照してください。

## 実行

リポジトリのルートで実行してください。Python 3.11以上、VS2022のC++ツールとWindows SDK、CMake/CTest 3.24以上が必要です。このチェックアウトの`cppbuild`を利用します。

```powershell
# ネットワークを使用せず、GoogleTest 1.14.0のZIPを指定
python -m usage_tests.run --gtest-archive C:/archives/v1.14.0.zip

# GoogleTestをダウンロードして全シナリオを実行
python -m usage_tests.run --online

# 選んだシナリオだけ実行（testing以外はGoogleTest不要）
python -m usage_tests.run --scenario lifecycle --scenario libraries

# 保存先を明示（存在しないディレクトリを指定）
python -m usage_tests.run --scenario external --output .test-work/my-external-check
```

既定の出力先は`.test-work/usage-日時/`です。同じ保存先の上書き・削除は行いません。再実行では新しい保存先を使ってください。`testing`を含む場合、ZIPか`--online`の指定が必須で、未指定のままテストを省略して成功にはしません。

## シナリオ

| 名前 | 作成するプロジェクトと確認内容 |
| --- | --- |
| `lifecycle` | 日本語・空白を含むパスのConsole。環境診断、Debug/Release、C++17/20、コンパイル定義の保存、親設定継承、再open時のビルド設定初期化、個別update/build/run/rebuild/clean、ソース移動・追加・削除、コンパイル失敗と復旧、info、保存競合とreload、Project登録解除、重複登録・範囲外パスの拒否。 |
| `libraries` | Headers → Math → StaticApp/SharedApp。同一Projectの静的／共有ライブラリ、ヘッダー専用依存、公開定義の伝播、推移的依存、DLLを利用した実行、Debug/Release、対象指定・並列ビルド／実行、全体build/rebuild/clean、利用側cleanによる依存DLLの保護、参照中Projectの解除拒否。 |
| `external` | ProviderとConsumer。別Solutionの主Projectへのリンク、外部側のDebug/Release指定、構成別ImportedLibraryへの切り替え、再open、外部バイナリーを保護するclean、ローカルCMakeSource/CMakePackage、プロジェクト／システムPCHと解除、unlink後に外部CMakeが読み込まれないこと。 |
| `execution` | First/Failure/Last。空白を含む引数、終了コード7の検出、指定順・失敗時停止／継続、並列・非同期実行とwait、Project単独の非同期実行、ToolSettingsの環境変数が実行プログラムへ届くこと。 |
| `templates` | Original/Template/Restored。ファイル素材作成・置換展開・別名登録・登録解除、イベントによる追加ファイル生成、再帰通知抑止、before/after通知・解除、Solutionテンプレートからの復元と実行、生成物／非保存設定の除外。 |
| `testing` | 共有LibraryとPassing/Failing/Empty。GoogleTest/CTest、Debug/Release、成功・skip・意図的な失敗・0件、全体テストの選択と停止／継続、個別testの独立性、修正後の再テスト、全体.sln経由のビルド。 |

## 結果の読み方

- `summary.json`：シナリオ別成否、所要時間、記録した操作・確認の数。
- 各シナリオの`steps.json`：操作結果、コマンド、終了コード、ビルド／実行出力、テストケースなど。
- 予期しない失敗時の`failure.txt`：Pythonのトレースバック。
- 各Solution／Projectの`.cppbuild/`：保存設定、CMake／VS生成物、ビルド成果物。

コンパイルエラー、終了コード7、GoogleTestの失敗・0件は意図的に発生させるケースです。それらが正しく失敗として返されることを確認できればシナリオはPASSになります。最後の終了コードは全シナリオ成功なら0、予期しない失敗があれば1です。一つのシナリオが失敗しても残りは実行します。

これは主要機能を組み合わせた利用検証で、あらゆる入力の網羅ではありません。既定アーキテクチャはx64です。x86/ARM64、VS IDEのGUI操作、ネットワーク障害、全例外パターンはこのシナリオ集の検証対象外です。

## 生成したプロジェクトを続けて使う

リポジトリルートのPythonから、表示された出力先に合わせて実行できます。

```python
from cppbuild import Solution, SolutionBuildSettings

solution = Solution.open(
    '.test-work/usage-日時/libraries/Solution/.cppbuild'
)
solution.set_build_settings(SolutionBuildSettings(
    configuration='Release',
    build_projects=['StaticApp', 'SharedApp'],
    run_projects=['StaticApp', 'SharedApp'],
))
report = solution.run()
print(report.success)
for process in report.processes:
    print(process.output)
```

非保存のビルド設定は再open後に再指定します。複数種類を持つMathを単独で操作する場合は`ProjectBuildSettings(project_type=ProjectType.STATIC_LIBRARY)`等で種類を指定してください。テストProjectのローカルGoogleTest ZIP指定も再open後に必要です。

`external`の最終状態は外部依存の登録を解除した状態、`testing`のFailingは修正済み、Emptyは0件のままです。元の失敗状態は`steps.json`に残ります。初期状態から再現する場合は新しい保存先でシナリオを再実行してください。
