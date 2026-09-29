# CppBuild

CMakeをラップし、C++のSolution／ProjectをPythonから管理するライブラリです。個別Projectの生成物を全体と共用し、Visual Studio 2022で生成・ビルド・実行・テストを行います。

## 動作環境

- Windows、Python 3.11以上
- CMake／CTest 3.24以上（PATHから利用可能、またはToolSettingsで指定）
- Visual Studio 2022のC++開発ツールとWindows SDK

Python実行時の外部パッケージ依存はありません。パッケージのビルドにはsetuptoolsが必要です。

## インストールと利用例

リポジトリを取得し、ルートで実行します。

```powershell
python -m pip install .
python -m examples.independent_project .test-work/demo
python -m examples.google_test .test-work/google-test
```

作成先には新しいディレクトリを指定してください。GoogleTestの初回構成では固定版1.14.0をGitHubから取得し、SHA256を検証します。オフラインでは同じ版のZIPを指定できます。

```powershell
python -m examples.google_test .test-work/offline-test C:/archives/v1.14.0.zip
python -m examples.complete_workflow .test-work/workflow
```

利用例はリポジトリ内で実行します。wheelにはライブラリ本体のみを含めます。詳しくは[利用手順](deliverables/USAGE.md)を参照してください。

## 実装範囲

- Solution／Projectの管理・設定保存、非保存ビルド設定と継承
- 個別生成・全体.sln統合、内部／外部依存、静的／共有ライブラリ、PCH
- GoogleTest／CTest、実行順・並列数・非同期待機
- テンプレート、イベント、環境診断、観測済み情報の取得
- 全体.slnのソリューションフォルダー配置、リンク先Solutionの全Project表示
- GUIDでのProject識別、名前付き外部リンクの自動登録・共用・未使用参照の自動削除
- 設定ファイル基準の相対パス保存と旧形式の自動移行

対象はVS2022です。他のgeneratorやVS IDEのGUI表示は検証していません。バージョン0.1.0の初期実装であり、制約・検証範囲は[実装記録](deliverables/IMPLEMENTATION_STATUS.md)に記載しています。

## 開発と資料

- [開発・貢献手順](CONTRIBUTING.md)
- [公開APIによるC++利用検証](usage_tests/README.md)
- [API設計](deliverables/API_DESIGN.md)
- [資料一覧](deliverables/DELIVERABLES.md)
- [マイルストーン](deliverables/MILESTONES.md)

`cppbuild/`はライブラリ、`tests/`はテスト、`examples/`は利用例です。`deliverables/`は現行資料、`references/`は設計履歴です。履歴内の旧仕様は現行仕様として扱いません。

## ライセンス

[MIT License](LICENSE)。著作権表記は`Copyright (c) 2026 CppBuild contributors`です。
