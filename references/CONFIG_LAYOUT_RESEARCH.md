# 設定ファイルと設定用ディレクトリの配置調査

調査日：2026-09-16。公式資料で確認した事例の比較であり、採用率の統計ではない。

調査後の決定：設定ファイルと関連ファイルをプロジェクトごとの `.cppbuild` にまとめ、JSON形式の `.cppbuild/project.json` を基本情報と読み込みの入口、`.cppbuild/templates/` をファイル用テンプレートの保存先とする。設定を単一ファイルに集約する必要はなく、役割に応じて分割できる。追加のファイル名・分割単位・相対パスの基準は未確定。以下は判断に至った調査と検討事項を記録したもの。

## 公式資料で確認した配置

| ツール | 配置 | 用途 |
| --- | --- | --- |
| CMake | プロジェクト直下の `CMakePresets.json` | 共有するビルド設定。別ファイルのincludeにも対応。 |
| Cargo | パッケージの `Cargo.toml` | パッケージの定義。 |
| Cargo | `.cargo/config.toml` | Cargoの動作に関する設定。 |
| VS Code | 単一フォルダーのワークスペースでは `.vscode/settings.json` | プロジェクトごとのエディター設定。 |

出典：

- [CMake Presets](https://cmake.org/cmake/help/latest/manual/cmake-presets.7.html)
- [Cargo Manifest](https://doc.rust-lang.org/cargo/reference/manifest.html)
- [Cargo Configuration](https://doc.rust-lang.org/cargo/reference/config.html)
- [VS Code Settings](https://code.visualstudio.com/docs/configure/settings)

CMakeの標準の配置は直下だが、現在の公式資料では4.4から `--presets-file` による別ファイルの指定も説明されている。「どのバージョンでも直下以外は使えない」という制約ではない。

## 本プロジェクト向けの判断

両方式に実例がある。確認した範囲では、プロジェクトの定義を直下に置き、ツール固有の設定を専用ディレクトリ内に置く使い分けが見られる。どちらかを全用途の多数派と断定する根拠はない。

今回はライブラリ独自の設定とファイル用テンプレートを一か所にまとめる目的から、専用ディレクトリ内に共通設定ファイルも置く方式を推奨する。名前・形式は仮称。

```text
MyApp/
├── CMakeLists.txt
├── .cppbuild/
│   ├── project.json
│   └── templates/
│       └── header.h
├── include/
└── src/
```

CMake用のファイルはCMake側の配置規則に従う。この提案はライブラリ独自の設定・素材をまとめるもの。

## 採用する場合に見直す事項

- 設定用ディレクトリを固定配置にするなら、設定ファイル自身の所在から特定できるため、直前に追加した `config_directory` 項目は原則不要になる。
- `Project.open` に設定ファイルを渡す入口は維持できる。例：`Project.open("MyApp/.cppbuild/project.json")`。
- 従来の「相対パスは設定ファイルの所在基準」を維持すると、ソースは `../src`、テンプレートは `templates/header.h` になる。
- ソースを `src` と書きたい場合は、専用ディレクトリの親をプロジェクトルートとして決め、相対パスの基準を変更する必要がある。固定配置ならルート引数の追加は不要。配置とパス解決の規則はセットで決める。
- プロジェクトテンプレート内の設定ファイルの探索位置、作成時の配置、依存先の設定ファイルパスの例も更新する必要がある。

配置方針と単一ファイル集約を必須にしない点はAPI_DESIGN.mdおよびFEATURE_REQUESTS.mdへ反映済み。上記の相対パスの基準や設定探索の詳細は引き続き検討する。
