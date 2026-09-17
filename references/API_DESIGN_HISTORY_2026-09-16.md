# 利用側APIの設計案

> 2026-09-16の再考前に保存した履歴。旧案・矛盾・撤回済みの解釈を含む。現在の仕様検討には [API_DESIGN.md](../deliverables/API_DESIGN.md) を使う。

## ユーザー確認済みの変更（以下の従来案より優先）

レビューは、ジャンル内のAPIをまとめて提示し、ジャンル単位で用途・引数・戻り値・主な挙動をユーザーに確認する。一つずつ個別の承認を求める進め方にはしない。内部形式や実装詳細の検討はAPIの確認に必要な範囲にとどめる。ジャンル5まで基本方針を確認済み。`set_source_directories` の専用APIとしての採否は保留。次はジャンル6「依存・リンク」をまとめて確認する。

本ライブラリはPythonライブラリとして設計する。以下に残るC++風の宣言は従来の検討用表記であり、ジャンルごとの確認に合わせて更新する。

### 最新の確認済み方針：ProjectとTargetそれぞれの設定管理

主ターゲットを示す `main_target` はプロジェクト全体の設定として `project.settings` が管理する。所属ターゲット名を参照し、そのターゲットの種類やコンパイル設定などの詳細は対応する `target.settings` が管理する。

`Project` と `Target` を独立したクラスとして用意し、それぞれが `settings` メンバーを持つ。`project.settings` はプロジェクト全体の設定、`target.settings` はそのターゲット固有の設定の取得・変更・保存・再読み込みを担当する。従来の「ターゲット設定もすべて `project.settings` に集約する」案より、この方針を優先する。

`Project` は所属ターゲットや主ターゲットを含む全体を管理し、`Target` は一つの実行ファイル・ライブラリ等を表す。ターゲットの種類、ソース対象ディレクトリ、コンパイル設定等は `target.settings` 側で扱う。保存先もプロジェクト設定と各ターゲットディレクトリの設定に対応させる。設定管理クラスの具体名、取得・保存APIの型、ターゲットの追加・取得・解除APIの配置は、この構成に合わせて見直す。

`Project.open(config_directory)` は所属ターゲットの設定も自動で読み込み、対応する `Target` オブジェクトを用意する。利用側にターゲットごとの再登録・個別読み込みを要求しない。設定の再読み込みはCMake再生成と分ける方針を維持する。プロジェクト設定全体の再読み込みと既存のTarget参照の整合性など、詳細は別途設計する。

以下の以前の確認済みAPI一覧は検討経緯として残すが、ターゲット固有の設定操作の配置はこの最新方針に従う。ジャンル6「依存・リンク」もこの構成で引き続き確認する。

確認済みの入口の変更：`Project.open("C:/work/MyApp/.cppbuild")` のように、プロジェクトの設定用ディレクトリを渡して開く。内部で `project.json` と必要な分割設定を読み込み、保存済みのターゲット・ソース対象ディレクトリ等を自動で取得してProjectを初期化する。利用開始のために `set_source_directories()` を別途呼ぶ必要はない。これは保存済みの構成を読み込む操作であり、CMakeの再生成は `update()` が担当する。従来の設定ファイルパスを直接渡すopen案より、このディレクトリ指定を優先する。

設定管理は `project.settings` オブジェクトが担当し、設定の取得・変更・保存・再読み込みをそこへまとめる。従来の `project.settings()`、`project.save_settings(...)`、`project.reload()` 案よりこの方針を優先する。`project.settings.reload()` は設定だけを読み直す操作とし、CMakeによる再生成は `project.update()` が担当する。ジャンル3で以下のAPI構成を基本合意とする。名前・型の詳細は今後の設計に応じて見直せる。

| API | 役割・戻り値 |
| --- | --- |
| `project.settings.get()` | 読み込み済み設定のコピーを `ProjectSettings` として取得する。 |
| `project.settings.save(values)` | 設定全体を検証・保存しProjectへ反映する。`ChangeReport` を返す。 |
| `project.settings.reload()` | 保存済み設定を再読み込みする。戻り値は `None`、失敗はエラーとする。 |
| `project.settings.add_target(settings)` | ターゲット定義を追加する。`ChangeReport` を返す。 |
| `project.settings.set_target_settings(name, settings)` | 指定ターゲットの設定を置き換える。`ChangeReport` を返す。 |
| `project.settings.remove_target(name)` | ターゲット定義を削除し、ソースファイルは残す。`ChangeReport` を返す。 |
| `project.settings.set_build_profile(profile)` | 名前付きビルド構成を追加・更新する。`ChangeReport` を返す。 |
| `project.info()` | 設定、認識済みファイル、保持するビルド情報を `ProjectInfo` として返す。 |

変更操作は検証して保存する。不正な設定や保存失敗はエラーとし、保存失敗時にメモリ上だけ新設定へ切り替えない。取得した設定コピーの変更だけではProjectは変わらない。これらの設定操作・情報取得ではCMakeの再生成やビルドを行わず、CMakeへの反映は更新・ビルド操作が担当する。`info()` は保持している情報を返すため、常に最新であるとは限らない。

ターゲットはビルドする実行ファイルやライブラリの単位であり、リンクはそれらを結び付ける操作。一つのプロジェクトに本体・テスト・補助ツールなど複数のターゲットを持てる。標準タイプでのプロジェクト作成は対応する主ターゲットを一つ用意する想定とし、カスタムテンプレートに複数ターゲットを含めれば作成時にまとめて用意できる。以下に残る取得・保存API等の未確認表記は、この基本合意より前の検討記録とする。

設定に関する機能は設定管理クラスに持たせる。ターゲット設定やビルド構成の設定もこのクラスが担当し、従来案のProject直下の設定操作は配置を見直す。`Project` はビルド・実行・CMake更新など、プロジェクト全体の操作を担当する。

### 確認済み：プロジェクト設定とターゲット設定の配置

プロジェクト全体の設定とは別に、各ターゲットのディレクトリに、そのターゲット専用の設定ファイルを置く。プロジェクト設定は全体の構成・主ターゲット・所属ターゲットの参照を管理し、ターゲット設定はそのターゲットの名前・種類・ソース対象ディレクトリ・コンパイル設定などを管理する。具体的なファイル名と、ターゲットディレクトリ直下に置くか専用サブディレクトリ内に置くかは未確定。

`Project.open(config_directory)` はプロジェクト設定から各ターゲット設定も読み込み、利用側にターゲットごとの再登録・個別読み込みを要求しない。設定管理APIは引き続き `project.settings` が担当し、設定値を変更する際の保存先の振り分けはライブラリが扱う。取得する設定データにターゲット情報を含めることは、保存先を単一ファイルに集約することを意味しない。

従来の「設定ファイルをすべてプロジェクト直下の `.cppbuild` 内へまとめる」という配置案より、このターゲット単位の配置方針を優先する。プロジェクトテンプレートには必要なターゲット設定も含める。ターゲット設定内の相対パスについては、これまでのプロジェクトルート基準からターゲットディレクトリ基準へ変えるかを別途検討し、まだ変更を確定しない。

### 確認済み：主ターゲットの設定

プロジェクトの本体となるターゲットを示す設定項目を設ける。Python側の項目名は `main_target` の案とし、ターゲット名を参照する。主ターゲットは実行ファイルまたはライブラリ等の既存ターゲットを指し、新しいターゲット種類を意味しない。標準タイプでのプロジェクト作成時は、作成する本体ターゲットを主ターゲットとして初期設定する。

主ターゲットと、外部へ公開するターゲット・既定のリンク先ターゲットは役割を区別する。主ターゲットに指定しただけで外部公開やリンク可能性を保証するものではない。既定のリンク先への自動採用、未指定を許すか、削除・名前変更時の扱い、専用の設定APIの有無は引き続き検討する。C++風の設定データ案では `mainTarget` として示す。

本ライブラリはCMakeをラップしてプロジェクト管理を行う。プロジェクト作成もCMakeを利用する前提とし、「作成だけならCMake不要」という要件は設けない。テンプレートの生成処理にCMakeスクリプトを使う案も、この前提で検討する。具体的なテンプレート形式・引数の定義・実行APIは未確定。

プロジェクト作成は `Project.create` に統一し、必須引数 `project_type` でタイプを指定する。標準タイプとして `ProjectType.STATIC_LIBRARY`、`ProjectType.EXECUTABLE`、`ProjectType.SHARED_LIBRARY` を提供する。

```python
project = Project.create(
    destination_directory="C:/work/MyApp",
    project_name="MyApp",
    project_type=ProjectType.EXECUTABLE,
)

custom_type = CustomProjectType(
    template_directory="C:/templates/my_app",
)
project = Project.create(
    destination_directory="C:/work/MyApp",
    project_name="MyApp",
    project_type=custom_type,
)
```

カスタムタイプはオブジェクトとして直接渡し、再利用できる。事前登録を必須とせず、タイプ登録APIは現段階では設けない。`CUSTOM` と別のテンプレートパス引数を組み合わせる方式も採用しない。

テンプレートによるプロジェクト作成専用APIと、作成時の `variables` 引数は設けない。プロジェクト名の反映には `project_name` を使う。プロジェクトテンプレートの配置・設定ファイルの特定方法と作成APIは以下の確認済み事項に従う。置換規則などの内部形式の詳細は引き続き確認する。

### 確認済み：プロジェクトごとの設定用ディレクトリ

設定ファイルと、このライブラリで使用するファイルを、プロジェクトごとの設定用ディレクトリにまとめる。[配置調査](CONFIG_LAYOUT_RESEARCH.md)を踏まえて採用した方針。

設定を一つのファイルに集約することは必須としない。プロジェクトの基本情報、ビルド構成、ファイル用テンプレートの登録情報などは、役割に応じて分割できる。ただし、分割そのものを必須とはせず、具体的な分割単位・ファイル名・形式はジャンル3で決める。

`Project.create` は作成先に設定用ディレクトリを用意し、その中に必要な設定ファイルを生成する。固定配置から所在を特定できるため、先に提案した `config_directory` 保存項目は設けない。設定の分割・保存・読み込みはライブラリが扱い、利用側がAPIに渡すためだけに個別の設定ファイルを用意する必要はない。

設定用ディレクトリ名は `.cppbuild`、基本情報と読み込みの入口はJSON形式の `.cppbuild/project.json`、ファイル用テンプレートの保存先は `.cppbuild/templates/` とする。以下の `build_profiles.json` は分割例であり、追加のファイル名・分割単位は未確定。

```text
MyApp/
├── .cppbuild/
│   ├── project.json
│   ├── build_profiles.json   # 必要に応じて分割する例
│   └── templates/
│       └── header.h
├── include/
└── src/
```

ファイル用テンプレートの作成・追加先は、この設定用ディレクトリ配下とする。登録情報もその中の設定に保存するが、基本情報と同じファイルに保存することは必須としない。作成時の実ファイルの格納・登録・保存は、以下の `Project.create_file_template` にまとめる。

プロジェクトを開く入口は `Project.open("C:/work/MyApp/.cppbuild")` のように設定用ディレクトリのパスを指定する。分割した設定の探索・参照方法はジャンル3で設計する。設定内および開いた `Project` に渡す相対パスは、`.cppbuild` の親であるプロジェクトルート基準とする。設定ファイルを分割しても基準は変えない。`Project.open` や `Project.create` に渡すプロジェクトを開く前の相対パスは、呼び出し側の作業ディレクトリ基準とする。以下の従来案に残る「単一の共通設定ファイルへの保存」「プロジェクト直下への設定ファイル配置」は、この方針に合わせて見直す対象とする。

プロジェクトからテンプレートを作成する際は、設定用ディレクトリ内の再利用に必要なファイルもコピー対象に含める。そこから作成するプロジェクトは、自身の設定用ディレクトリにそれらのファイルを持つ。ビルド成果物は引き続き除外する。

### 確認済み：プロジェクトテンプレートの配置と設定ファイルの特定

テンプレートの直下に、プロジェクトと同じ配置の設定用ディレクトリを置く。`CustomProjectType(template_directory=...)` で指定したディレクトリ内の設定用ディレクトリから必要な設定を読み込み、ソースの配置などを取得する。必須の設定ファイルがない場合や内容が不正な場合は、理由を示してエラーにする。

`TemplateTools.create_project_template` もこの配置で出力し、手作業で作成するテンプレートにも同じ規則を適用する。読み込みの入口はテンプレート内の `.cppbuild/project.json` とする。

### 確認済み：既存プロジェクトからのテンプレート作成

```python
custom_type = TemplateTools.create_project_template(
    project=project,
    destination_directory="C:/templates/my_app",
)
```

既存プロジェクトの設定とソースをコピーし、ビルド成果物は除外する。プロジェクト名は次回の作成時に置換できる形にする。作成先を参照する `CustomProjectType` を返す。既存ファイルと衝突した場合はエラーにする。手作業で作成したテンプレートも `CustomProjectType(template_directory=...)` で利用でき、この作成APIの経由は必須ではない。

プロジェクトテンプレートの作成者にCMakeの知識やスクリプトの手書きを要求しない。必要なCMake処理はライブラリ側で扱う。当面、テンプレートからプロジェクトを作成する際に自動反映する値は `project_name` に限定する。それ以外の設定、ファイル名・ディレクトリ名、ソース内容を推測して自動変更する機能は設けない。プロジェクト名と同じ文字列が含まれるだけで、一律に置換することも前提にしない。プロジェクト名の反映箇所・内部表現の詳細は別途設計する。

プロジェクトテンプレート向けの独自引数、任意のPython/CMake生成処理の登録は現段階では採用しない。以下のファイル用テンプレートの置換案とは別の方針として扱う。

### 確認済み：ファイル用テンプレートの作成・登録・保存

```python
project.create_file_template(
    name="header",
    source_file="include/example.h",
)
```

`Project` の操作として、元ファイルをそのプロジェクトの設定用ディレクトリ内のテンプレート保存先へコピーし、指定した名前で登録して登録情報を保存する。利用側による保存先パスの指定や、作成後の別途登録・保存は不要。同じ登録名または保存先ファイルが既に存在する場合は、上書きせずエラーにする。

元ファイル内の文字列を置換用の記号に変換する機能も設計対象に含める。一度保留したが、ユーザーの最新の意向により再開した。指定しなければ元ファイルの内容をそのままコピーして登録・保存する。元ファイル自体は変更しない。

具体的なAPIは、任意の `replacements` 引数を追加する案とする。以下の辞書の向き・記号の文法・置換範囲は提案段階であり、詳細は引き続き検討する。

```python
project.create_file_template(
    name="header",
    source_file="include/example.h",
    replacements={"class_name": "Example"},
)
```

この案では、キーが置換項目名、値が元ファイル内の文字列を表す。`Example` を `{{class_name}}` に変換した内容をテンプレートとして保存する。テンプレートを使ったファイル追加時に `class_name` へ実際の名前を指定する。使用時のAPIは別途設計する。

保存先ファイルの命名規則、戻り値、途中失敗時の扱いは未確定。`source_file` の相対パスはプロジェクトルート基準とする。

従来案の `TemplateTools.createFileTemplate` はこの `Project.create_file_template` に置き換える。既存のテンプレートを登録する `set_file_template`、取得・解除APIは維持する。

### 確認済み：ファイル用テンプレートの登録・取得・解除

```python
project.set_file_template(
    name="header",
    template_file="C:/work/MyApp/.cppbuild/templates/header.h",
)
templates = project.file_templates()
project.remove_file_template(name="header")
```

`set_file_template` は名前を付けて登録し、同じ名前があれば更新する。`file_templates` は登録情報を取得する。`remove_file_template` は登録のみを解除し、テンプレートの実ファイルは削除しない。

登録情報はプロジェクトの設定用ディレクトリ内に保存する。保存するファイルと登録変更・解除時の保存タイミングはジャンル3で確認する。`create_file_template` は作成操作内で登録情報の保存まで行う。上の例は絶対パスで示す。相対パスなら `.cppbuild/templates/header.h` と指定する。ファイル追加時には `"header"` などの登録名で選択する。置換値の扱いや戻り値の具体的な型などは引き続き確認する。

以下の `ProjectTemplate`、`CreateOptions`、ファイル用テンプレート登録、任意の置換値に関する従来案は、この合意に合わせた見直しが必要。その他のジャンルはまだ確認中である。

現状のFEATURE_REQUESTS.mdを、利用側から呼び出すAPIにした検討用の案。実装・採用済み仕様ではない。C++風の宣言で表すが、実装言語・C++規格・ABI・具体的な型の実装は未決定。コード断片はコンパイル用ヘッダーではなく、型の細部や宣言順を省略している。

情報受け渡しの全ジャンル点検とOSS実装との比較は[API_REVIEW.md](API_REVIEW.md)を参照。一時ビルド構成と実行環境のデータ指定を本案へ反映し、その他の改善候補は調査資料に分けている。

## ジャンル別の目次

各ジャンルに、関連するクラス・引数の型・関数宣言・戻り値・主な挙動をまとめる。共通事項は冒頭、要望との対応や横断的な検討事項は付録に置く。ジャンル分けは資料内の分類であり、独立ライブラリへの分割を意味しない。

| ジャンル | 主なAPI |
| --- | --- |
| [1. プロジェクトの基本操作](#project) | create、open |
| [2. テンプレート](#templates) | createProjectTemplate、createFileTemplate、inspect、fileTemplates、setFileTemplate、removeFileTemplate |
| [3. 設定・情報](#settings) | project.settingsによる設定管理、project.settings.reload、info、ターゲット設定、ビルド構成設定 |
| [4. ファイル・ディレクトリ](#files) | setSourceDirectories、addFile、removeFile、moveFile |
| [5. CMake更新](#cmake-update) | update（走査とVSフィルター更新を含む） |
| [6. 依存・リンク](#dependencies) | linkProjects、unlink、外部ライブラリ向け候補API |
| [7. ビルド・実行](#build-run) | build、rebuild、clean、run |
| [8. テスト](#tests) | addTests、test |
| [9. PCH](#pch) | setPch、clearPch、createPchHeader |
| [10. 環境チェック](#environment) | Environment::check、checkEnvironment |
| [11. イベント](#events) | on、off |

## 共通事項：全体の使い方とクラス

| クラス・型 | 役割 |
| --- | --- |
| `Project` | 一つのプロジェクトを開き、設定・ファイル・依存・ビルド等を操作する窓口。内部を一つの実装モジュールにまとめる意味ではない。 |
| `ProjectSettings` | 共通設定ファイルに保存する構成。ターゲット、対象ディレクトリ、依存、ビルド構成等。 |
| `TargetSettings` | 一つのライブラリまたは実行ファイルの設定。テスト用実行ファイルもターゲットとして扱う案。 |
| `TemplateTools` | テンプレートの作成・内容確認。利用時にはテンプレートのパスを渡す。 |
| `Environment` | プロジェクトを開く前にも使える環境チェック。 |
| `Result<T>` | 成功値T、または理由と部分的な実行結果を含むErrorを返す。特定の標準型・外部ライブラリの採用は未定。 |

```cpp
auto opened = Project::open("C:/work/MyApp/.cppbuild/project.json");
if (!opened) { /* opened.error() を利用側で表示 */ }
auto project = std::move(opened.value());

auto linked = project.linkProjects("MyApp", {"C:/work/Math/.cppbuild/project.json", "C:/work/Utility/.cppbuild/project.json"});
if (!linked) { /* 中止・エラー表示 */ }

auto built = project.build({.profile = "vs2022", .configuration = "Debug"});
if (!built || !built.value().succeeded) { /* 中止・エラー表示 */ }

auto executed = project.run("MyApp", {
    .profile = "vs2022", .configuration = "Debug", .arguments = {"--demo"}
});
// executedの成否とアプリの終了コードを利用側で判断する。
```

`.cppbuild/project.json` は確認済みの読み込みの入口。`MyApp` はテンプレートで定義したターゲット名、`vs2022` は保存済みビルド構成の名前。エラー分岐は説明用の省略表記で、実際には処理を中断する。

### この案で置く共通ルール

- APIの情報受け渡しは値・構造体・一覧・コールバックを基本とする。共通設定ファイルやテンプレート等、ファイルで扱う明確な理由があるものはパスを使う。利用側に設定や結果を受け渡すためだけの専用ファイルを用意させない。
- 共通設定ファイルの存在は、全設定を個別ファイルで渡すという方針ではない。ライブラリ内部で必要なCMake用ファイルを生成・読込することも、利用側のファイル化の責務とは分ける。
- 設定変更APIは、検証した上で共通設定ファイルにも保存する案とする。呼ぶたびに別のsaveを要求しない。保存失敗時はメモリ上だけ新設定へ切り替えない。
- `build`等の引数は今回の操作の指定。共通設定ファイルの既定値は書き換えない。ただしCMakeのビルドディレクトリにはキャッシュ等が残る。
- 更新・ビルド・実行・テスト・プロジェクト別環境チェックでは、保存済み構成の名前だけでなく、BuildProfileの値を直接指定できる。実行するためだけの構成登録・設定ファイルへの保存は要求しない。
- 設定変更のCMake反映は `update` または次のビルド時に行う。ファイルの追加・削除・移動は操作内でCMakeの更新・再生成まで行い、別途 `update` を呼ぶことを要求しない。
- プロジェクトを開くAPIには `.cppbuild` のパスを渡す。独立したルートディレクトリの指定は不要。`create`のみ作成先ディレクトリを受け取り、その中に `.cppbuild/project.json` を生成する。
- `open`に渡す相対パスは呼び出し側の作業ディレクトリ基準。設定内および開いたProjectへ渡す相対パスは、プロジェクトルート基準とする。依存先の設定内のパスは依存先のプロジェクトルート基準。絶対パスも使える。`create`等のプロジェクトを開く前の相対パスは呼び出し側の作業ディレクトリ基準。
- まず同期APIとして示す。外部プロセスのログ通知・中止要求は共通引数で受け付ける。非同期実行の専用APIは必要性を別途検討する。
- Projectを破棄しても実ファイルは削除しない。同じProjectの操作は直列に呼ぶ。外部から設定ファイルが変更された場合は保存前に検出し、黙って上書きせず再読み込みを求める。

## 共通事項：引数・戻り値

```cpp
using Path = std::filesystem::path;
using TargetName = std::string;
using DependencyId = std::string;
using Variables = std::map<std::string, std::string>;

struct Diagnostic {
    Severity severity;       // Info / Warning / Error
    std::string code;
    std::string message;
    std::optional<Path> path;
};

struct ChangeReport {
    std::vector<Path> created, modified, removed;
    bool cmakeUpdateRequired;
    std::vector<Diagnostic> diagnostics;
};

struct ProcessReport {
    ProcessStage stage;                  // Configure / Build / Clean / Test / Run
    int exitCode;
    std::string standardOutput, standardError;
};

struct OperationReport {
    bool succeeded;
    std::vector<ProcessReport> processes;  // 起動した順序
    std::vector<Diagnostic> diagnostics;
};

struct Error {
    std::string code, message;
    ChangeReport completedChanges;       // ファイル操作等で完了した部分
    std::vector<ProcessReport> processes;
    std::vector<Diagnostic> diagnostics;
};

struct RuntimeEnvironment {
    std::optional<Path> cmakeExecutable, ctestExecutable;
    std::map<std::string, std::string> environmentOverrides;
};

struct ExecutionOptions {
    RuntimeEnvironment runtime;
    LogCallback onOutput;                 // void(OutputChannel, string_view)
    CancellationToken cancellation;       // 未指定なら中止要求なし
};
```

`Result<T>`の失敗は不正な設定、ファイル操作失敗、起動不能、中止など。起動できたCMake・ビルド・CTestの非ゼロ終了は`OperationReport.succeeded = false`で返す。後続工程は実行しない。アプリ実行の終了コードは意味を決めつけず`ProcessReport`で返す。

ログの保持上限やファイルへの転送方式は実装時に定める。常に無制限にメモリへ蓄積することは要求しない。

RuntimeEnvironmentは共通設定に保存しない実行時データ。実行ファイルの明示指定を優先し、未指定時は有効な環境から探索する。環境変数は子プロセスへ渡し、ライブラリ利用側のプロセス全体の環境は変更しない。相対実行ファイルパスはProject操作ではプロジェクトルート基準、単独のEnvironment::checkでは呼び出し側の作業ディレクトリ基準。CMake File API等の入出力は内部で扱い、利用側に結果ファイルの作成・解析を求めない。

<a id="project"></a>

## ジャンル1：プロジェクトの基本操作

担当クラスは`Project`。ここには作成・読み込みをまとめ、開いた後の設定取得・保存・再読み込みはジャンル3で扱う。各ジャンルのProjectメンバー宣言は同じクラスの一部分を示す。

```cpp
struct CreateOptions {
    std::string projectName;
    Variables variables;                  // テンプレートの置換値
    std::optional<bool> enableTests;      // 未指定ならテンプレートの既定値
};

class Project {
public:
    static Result<Project> create(
        Path destinationDirectory, ProjectTemplate projectTemplate, CreateOptions options);
    static Result<Project> open(Path configFile);
};
```

| API | 主な処理・戻り値 |
| --- | --- |
| `create` | テンプレートを展開し、作成先の `.cppbuild/project.json` と必要な設定・初期ファイルを作成して開く。追加の設定ファイルの分割・形式は未決定。戻り値はProject。CMakeを利用する前提とする。既存ファイルに衝突する場合は失敗。 |
| `open` | 指定した設定ファイルを検証して読み込み、ソース等の対象ディレクトリをその内容から取得する。CMake実行やビルドはしない。外部CMakeListsを独自形式へ自動変換する機能ではない。 |

プロジェクト全体の削除APIは設けない。必要な場合は利用側でファイル・ディレクトリを削除する。バックアップや独立した復元APIも設けない。個別ファイルの削除とクリーンは、それぞれジャンル4・7で扱う。

<a id="templates"></a>

## ジャンル2：テンプレート

担当クラスは`TemplateTools`。テンプレートの作成・確認を扱う。テンプレートを使ったプロジェクト作成はジャンル1、ファイル追加はジャンル4を参照。

プロジェクト用テンプレートはルートディレクトリと大元の設定ファイルの組で表す。`ProjectTemplate`はこの二つのパスを保持する構造体であり、ファイル内容を利用側で読み込んで渡す必要はない。相対的なconfigFileはテンプレートのdirectory基準。設定内のソース等の相対パスはプロジェクトルート基準とする。

テンプレート作成時に必要な設定・素材をコピーする。作成後のプロジェクトでも `.cppbuild/project.json` を入口とする配置を維持する。具体的な設定項目・変換規則は未決定。

```cpp
struct ProjectTemplate {
    Path directory;
    Path configFile;
};
struct TemplateOptions {
    std::vector<Path> includedPaths;       // 元ディレクトリ基準。含める範囲を指定
    Variables replacements;               // この案では置換キーと元の文字列
};
struct ProjectTemplateInfo {
    std::vector<Path> files;
    std::vector<std::string> requiredVariables;
    bool supportsTests;
};
struct FileTemplateInfo {
    std::vector<std::string> requiredVariables;
};

class TemplateTools {
public:
    static Result<ProjectTemplate> createProjectTemplate(
        ProjectTemplate source, Path destinationDirectory, TemplateOptions options);
    // ファイル用テンプレート作成は冒頭のProject.create_file_templateへ置き換え済み。
    // 作成時の文字列から置換記号への自動変換は、冒頭の任意引数案で検討する。
    static Result<ProjectTemplateInfo> inspect(ProjectTemplate projectTemplate);
    static Result<FileTemplateInfo> inspect(Path templateFile);
};
```

`createProjectTemplate`は既存のひな形のディレクトリと設定ファイルをsourceとして受け取り、指定範囲をコピーし、指定文字列を置換用の記号に変えてテンプレートを作る。戻り値は作成されたテンプレートのディレクトリと設定ファイルの組。`createFileTemplate`は単一ファイル版で、変更内容を返す。どちらも出力先の既存ファイルを既定では上書きしない。`inspect`はプロジェクト用では必要な置換値・含まれるファイル・テスト対応を、ファイル用では必要な置換値を返す。

テンプレートはエディタで直接作成してもよい。使用時に上記の作成APIを経由することは必須にしない。置換は明示したテキストとパスを対象とし、バイナリはそのままコピーする案。記号の文法、テキストの判定・文字コード、テンプレートのメタ情報の形式は未確定。

### プロジェクトごとのファイル用テンプレート登録

登録はプロジェクトの大元の設定ファイルに保存する。情報は単純な構造体の一覧として取得する案とし、専用の操作オブジェクトは設けない。

```cpp
struct FileTemplateSettings {
    std::string name;                     // header、source、test等の登録名
    Path file;                            // プロジェクトルート基準
    Variables defaultVariables;
};

// Projectのメンバー関数。
std::vector<FileTemplateSettings> fileTemplates() const;
Result<ChangeReport> setFileTemplate(FileTemplateSettings settings);
Result<ChangeReport> removeFileTemplate(std::string name);
```

`fileTemplates`は登録情報を値として返す。`setFileTemplate`は名前で登録・置換して保存する。`removeFileTemplate`は登録だけを削除し、テンプレートの実ファイルは削除しない。実ファイルを作るcreateFileTemplateとは別の操作。

ファイル追加時は`addFile("include/example.h", "header", options)`のように登録名を指定する。登録された既定の置換値より、その操作で渡した置換値を優先する。未登録名やテンプレートファイルの欠落はエラーとする。登録名・構造体・APIの細部は今回の具体化案。

<a id="settings"></a>

## ジャンル3：設定・情報

設定管理は `Project` が持つ `settings` オブジェクトにまとめる。管理クラスの名前は未定。設定値のデータ型案 `ProjectSettings`、`TargetSettings`、`BuildProfile` と、読み書きを担当する管理オブジェクトは区別する。

設定の保存先は、冒頭の確認済み事項に従い、プロジェクトごとの設定用ディレクトリとする。`ProjectSettings` というAPI上のデータ型と、保存時のファイル分割は別に設計する。分割時の読み込み・検証・保存失敗時の整合性も、このジャンルで検討する。

このジャンルの設定変更APIは構造体を直接受け取り、情報取得APIはデータを直接返す。利用側がターゲット用・ビルド構成用などの専用ファイルを作る必要はない。`saveSettings`も構造体を受け取ってライブラリが既存の共通設定ファイルに保存する操作であり、利用側がシリアライズしたファイルを渡す操作ではない。共通設定に保存する項目と変更時の即保存は、情報受け渡しの方式とは別の設計上の検討事項。

### 設定の取得・保存・再読み込みと情報取得

#### 確認済み：設定管理オブジェクトと再読み込み

```python
project.settings.reload()
```

`project.settings` は設定のコピーを返すメソッドではなく、プロジェクトに紐づく設定管理オブジェクトとする。取得・変更・保存・再読み込みをこのオブジェクトへまとめる。

`reload()` は `.cppbuild/project.json` を入口に必要な設定を読み直し、開いているProjectへ反映する。CMakeによる再生成やビルドは行わず、再生成は `project.update()` が担当する。

引数なし・戻り値 `None`、読み込みや検証の失敗はエラーとする案。登録済みイベントを維持し、設定に依存するファイル一覧・ビルド情報を再取得が必要な状態にする案も維持する。

以下の取得・保存は以前に確認した挙動の記録。メソッドの配置は今回の合意で変更するため、呼び出し方は再確認する。

#### 従来案：設定の取得 `Project.settings()`（設定管理オブジェクトへ移行）

```python
settings = project.settings()
```

引数なしで、現在読み込んでいる設定のコピーを `ProjectSettings` オブジェクトとして返す。プロジェクト名、ターゲット、ビルド構成などをまとめて取得でき、保存先が複数ファイルでも利用側で個別に読み込む必要はない。返されたオブジェクトを書き換えても、それだけではProjectや保存済み設定は変更されない。設定ファイルの再読み込みやCMakeの実行は行わない。

このAPIは現時点の暫定合意とし、今後の設計に応じた変更を許容する。

#### 従来案：設定の反映・保存 `Project.save_settings()`（設定管理オブジェクトへ移行）

```python
settings = project.settings()
# settings の必要な項目を変更する
report = project.save_settings(settings)
```

変更後の `ProjectSettings` を受け取り、設定を検証して必要な設定ファイルへ保存し、開いている `Project` にも反映する。複数の設定をまとめて変更するAPI。戻り値は変更内容を示す `ChangeReport` とし、その具体的な項目は今後検討する。

不正な設定はエラーにする。保存に失敗した場合も、メモリ上だけ新しい設定へ切り替えない。CMakeへの反映は更新・ビルド操作で行う。複数ファイルへの保存失敗時の整合性を保つ具体的な実装は別途設計する。

以下のC++風の宣言は従来の検討用表記。設定の取得・保存・再読み込みは `project.settings` 配下へ移す方針を優先する。情報取得APIは引き続き検討案。

```cpp
// Projectのメンバー関数。
ProjectSettings settings() const;
Result<ChangeReport> saveSettings(ProjectSettings settings);
Result<void> reload();
ProjectInfo info() const;
```

| API | 主な処理・戻り値 |
| --- | --- |
| `settings` | 保存対象の構成を値として返す。返した値の変更だけではProjectは変化しない。 |
| `saveSettings` | 構成全体を検証し、元の設定ファイルへ保存してProjectにも反映。ターゲット等の詳細操作を一括して行う経路。ソースファイル自体は追加・削除しない。 |
| `reload` | 同じ設定ファイルを再読み込み。イベント登録は維持し、ファイル一覧・ビルド情報は再取得が必要な状態にする。 |
| `info` | 設定パス、その所在ディレクトリ、設定内容、最後に認識したファイル一覧、ビルド情報の有無・更新時点を返す。最新であることを暗黙に保証しない。画面表示・独自形式への出力は利用側で行う。 |

`ProjectInfo`は設定を読み直しただけの状態と、CMake生成後の成果物情報を区別する。情報表示のためだけにCMakeを起動しない。

```cpp
enum class SnapshotState { NotLoaded, Available, Stale, Failed };
struct FileInfo {
    Path path;
    std::vector<TargetName> targets;
};
struct ArtifactInfo {
    TargetName target;
    std::string configuration;
    Path path;
};
struct BuildInfo {
    Path buildDirectory;
    SnapshotState state;
    std::vector<ArtifactInfo> artifacts;
    std::vector<Diagnostic> diagnostics;
};
struct ProjectInfo {
    Path configFile;
    ProjectSettings settings;
    SnapshotState filesState;
    std::vector<FileInfo> files;
    std::vector<BuildInfo> builds;
};
```

Availableは最後の取得に成功した状態であり、外部変更がないことを常時保証しない。設定変更・ファイル操作で関連する情報をStaleにする。失敗した更新を以前の成功値で隠さず、診断と状態を返す。成果物一覧は生成後のCMake File APIから内部取得する案で、実ファイルが既にビルド済みかは別途確認する。最新の配置取得と生成情報更新にはupdateを使う。

### 設定データとターゲット・ビルド構成の変更

```cpp
enum class TargetKind { Executable, StaticLibrary, SharedLibrary, HeaderOnlyLibrary };
enum class Visibility { Private, Public, Interface };

template<class T> struct UsageSetting { T value; Visibility visibility; };

struct TargetSettings {
    TargetName name;
    TargetKind kind;
    std::vector<Path> sourceDirectories;   // 配下すべてを表示・管理の対象とする
    std::optional<UsageSetting<int>> cppStandard; // 必要な最小C++規格と公開範囲
    std::vector<UsageSetting<Path>> includeDirectories;
    std::vector<UsageSetting<std::string>> compileDefinitions;
    std::vector<UsageSetting<std::string>> compileOptions;
    std::vector<UsageSetting<std::string>> linkOptions;
    std::optional<PchSettings> pch;
};

struct BuildProfile {
    std::string name;
    Path buildDirectory;
    std::optional<std::string> generator, architecture, toolset;
    std::optional<Path> toolchainFile;
    std::string defaultConfiguration;      // Debug等
    std::map<std::string, CMakeCacheValue> cmakeCacheVariables;
};

enum class CMakeCacheType { Bool, String, Path, FilePath };
struct CMakeCacheValue {
    CMakeCacheType type;
    std::string value;
};

// 未指定、保存済み構成名、今回だけの構成値のいずれか。
using BuildSelection = std::variant<std::monostate, std::string, BuildProfile>;

struct ProjectSettings {
    int formatVersion;
    std::string name;
    std::vector<TargetSettings> targets;
    TargetName mainTarget;                // 本体となるターゲット。未指定可否は未確定
    std::vector<TargetName> publishedTargets;
    std::optional<TargetName> defaultLinkTarget;
    std::vector<DependencySettings> dependencies;
    std::vector<BuildProfile> buildProfiles;
    std::vector<FileTemplateSettings> fileTemplates;
    std::optional<std::string> defaultBuildProfile;
};

// 以下はProjectのメンバー関数。
Result<ChangeReport> addTarget(TargetSettings settings);
Result<ChangeReport> setTargetSettings(TargetName target, TargetSettings settings);
Result<ChangeReport> removeTarget(TargetName target);
Result<ChangeReport> setBuildProfile(BuildProfile profile);
```

各変更は設定ファイルへ保存する。`addTarget`は同名が存在すれば失敗。`setTargetSettings`は指定ターゲットの設定を置き換え、名前変更は扱わない。`removeTarget`はターゲット定義を削除し、ソースは残す。他の設定から参照中なら理由を返し、暗黙に関連設定を削除しない。

cppStandardはtarget_compile_featuresのcxx_std_Nへ対応させ、公開ヘッダーが必要とする規格をPublic/Interfaceで利用先へ伝えられるようにする。これは最小規格の要求であり、厳密にその規格だけでビルドする指定ではない。ヘッダーのみのターゲットでは設定の公開範囲をInterfaceとし、コンパイルを伴うPrivate/Public設定は診断する。名前変更をsaveSettingsで行う場合も参照の整合性を検証する。

CMakeCacheValueはCMakeへ渡す型を明示するデータで、利用側がCMake構文を組み立てる必要はない。Bool値・パスを検証・変換し、文字列のエスケープは内部で扱う。generator等の専用項目と同じ意味のキャッシュ変数が矛盾する場合は診断し、暗黙の上書き順に依存しない。全CMake型や式を独自に再実装することは目的にしない。

`setBuildProfile`は名前で追加・置換する。ターゲットの追加や型の分け方は、本体とテストを扱うために今回具体化した案。対象ディレクトリを変更する専用APIはジャンル4を参照。

共通設定のパスは可能な範囲で相対化する。PC固有のツール配置はEnvironmentCheckOptionsや実行環境で指定し、BuildProfileとCMake Presetsとの連携・保存分担は未確定。

BuildSelectionでBuildProfileを直接渡す場合は保存済み構成との暗黙のマージをせず、その値を使う。nameは登録しないため空でよい。既存構成を一部変更して試す場合はsettings()から取得した値を変更して渡せる。未指定は保存済みの既定構成を使い、それもない場合は不足する指定を診断する。CMake Presetsや独自の一時設定ファイルを利用側に作らせない。

<a id="files"></a>

## ジャンル4：ファイル・ディレクトリ

### 確認済みのAPI構成

`add_file`、`remove_file`、`move_file` は共通の任意引数 `auto_update=True` を持つ。既定ではファイル操作・管理情報更新に加えてCMake更新まで実行する。`auto_update=False` の場合もファイル操作と管理情報更新は行い、CMakeへの反映だけを保留する。複数操作をまとめた後に `project.update()` で反映できる。以下の自動更新の記述は、この既定動作を示す。

```python
project.add_file("src/a.cpp", content="", auto_update=False)
project.add_file("src/b.cpp", content="", auto_update=False)
project.update()
```

| API | 操作 |
| --- | --- |
| `project.add_file(destination, template_name=...)` | 登録済みテンプレートからファイルを作成する。 |
| `project.add_file(destination, content=...)` | 指定したテキスト内容でファイルを作成する。 |
| `project.remove_file(path)` | 指定した実ファイルを削除する。 |
| `project.move_file(source, destination)` | ファイルを移動・名前変更する。 |

ファイルの追加・削除・移動は、管理情報の更新に加えてCMake用ファイルの更新とビルドシステムの再生成まで自動で行う。Visual Studioではソリューション内の対応するプロジェクトとフィルターへ変更を反映し、利用側に別途 `update()` の呼び出しを要求しない。コンパイルは行わない。パスはプロジェクトルート基準とする。

対象ターゲットへの所属は保存済みのソース対象ディレクトリ設定に従う。対象外の資料等を追加した場合に、勝手にビルド対象へ組み込む意味ではない。自動更新に使うビルド構成の選択方法と、ファイル変更・CMake実行結果をまとめる戻り値の詳細は今後設計する。CMake更新に失敗した場合は操作全体を成功扱いせず、完了済みのファイル変更と失敗理由を伝える。

保存済みのターゲットやソース対象ディレクトリは `Project.open(config_directory)` が自動で読み込み、Projectインスタンスを作成する。後から対象を変更するための `project.settings.set_source_directories(...)` は専用API案として残すが、読み込みに必須の操作ではなく、今回確認したファイル操作の確定一覧にも含めない。

担当クラスは`Project`。対象ディレクトリの指定と、実ファイルの追加・削除・移動を扱う。CMakeへの反映はジャンル5を参照。

### 対象ディレクトリの指定

`open()` で保存済みの対象ディレクトリを自動取得するため、以下は読み込み時の必須手順ではない。開いた後に対象を変更するための設定API案であり、配置は `project.settings.set_source_directories(...)` とする。専用APIとして残すかはジャンル4の確認中。

```cpp
Result<ChangeReport> setSourceDirectories(TargetName target, std::vector<Path> directories);
```

指定ターゲットの対象ディレクトリ一覧を置き換え、設定ファイルへ保存する。配下のファイルを再帰的に対象とする。実ファイルを移動・削除する操作ではない。戻り値は設定の変更内容とCMake更新の必要性を含む。実際の再走査とVSフィルター更新は`update`で行う。

### ファイルの追加・削除・移動

```cpp
struct AddFileOptions { Variables variables; bool overwrite = false; };
struct FileContent { std::string utf8Text; }; // データから作成するテキストファイル
struct CreateFileOptions { bool overwrite = false; };
struct MoveFileOptions { bool overwrite = false; };

Result<ChangeReport> addFile(
    Path destination, std::string templateName, AddFileOptions options = {});
Result<ChangeReport> addFile(
    Path destination, FileContent content, CreateFileOptions options = {});
Result<ChangeReport> removeFile(Path file);
Result<ChangeReport> moveFile(Path source, Path destination, MoveFileOptions options = {});
```

- `addFile`：登録名からテンプレートを展開する経路と、渡されたテキスト内容をそのまま作成する経路を用意する。後者は空ファイルや利用側で生成した内容にも使え、テンプレートの作成・登録は不要。
- `removeFile`：指定した実ファイルを削除する。再帰的なディレクトリ削除をこの関数に含めない。
- `moveFile`：ファイルを移動する。対象ディレクトリ間の移動にも対応し、認識した一覧を更新する。ソース内の`#include`や任意のコードの自動書換えはしない。
- ファイル変更を認識済み一覧へ反映し、同じ操作内でCMake更新・再生成を行う。所属情報がディレクトリから決まる場合、ファイルごとの保存項目は不要。
- ファイル操作後の管理情報更新やコールバックに失敗した場合は、Errorに作成・移動等の完了部分を返す。複数ファイル・任意コールバックを含む完全なロールバックは保証しない。

ソース対象ディレクトリ外という理由だけで操作を拒否しない。資料や補助ファイルも指定したパスで扱える。対象外に作成したファイルを勝手にターゲットへ追加せず、ソース対象への出入りに応じて一覧を更新する。テンプレート版・内容版のどちらも新規作成後はFileAddedを通知し、上書きだけでは追加イベントを発生させない。バイナリの汎用編集APIは現時点では追加しない。

<a id="cmake-update"></a>

## ジャンル5：CMake更新

公開APIは `project.update(...)` にまとめ、独立した `scan_files()` は設けない。走査自体は内部処理として残す。`add_file`、`remove_file`、`move_file` は操作内で必要な配置認識とCMake更新まで自動実行する。`update()` は外部エディター等によるファイル変更や設定変更を明示的に反映するために使う。引数・戻り値の詳細は引き続き検討案。

ファイル操作の自動更新は `auto_update=True` が既定。`False` で保留した変更をまとめて反映する場合にも `update()` を使う。

担当クラスは`Project`。配置の再認識、CMakeへの反映、VSフィルターの更新をまとめて扱う。

```cpp
struct UpdateOptions {
    BuildSelection profile;               // 未指定なら既定。名前または構成値も可
    std::string configuration;             // 空ならプロファイルの既定値
    std::vector<Path> scanDirectories;     // 空なら設定された全対象。部分再走査も可能
};
struct UpdateReport {
    OperationReport execution;
    ChangeReport changes;
    std::vector<Path> recognizedFiles;
    Path buildDirectory;
};

Result<UpdateReport> update(UpdateOptions options = {}, ExecutionOptions execution = {});
```

`update`は配置の再走査、CMake用入力の更新、CMakeのconfigure/generateまで行う案。ビルドはしない。Visual Studioジェネレーターの場合は、この処理でフィルターも更新する。CMakeの失敗は`execution.succeeded = false`で返し、更新完了として扱わない。

走査は更新処理の内部で行い、Projectの認識済み一覧を更新する。利用側に別途走査操作を要求しない。移動の自動認識は削除と追加として扱ってよく、内容からの同一ファイル推定は要求しない。

対象ディレクトリ配下はすべて収集し、相対配置を表示へ反映する。通常のC++ソースはコンパイル対象、ヘッダーや資料は表示対象として扱う。外部ツール向けファイル等の細かな分類は未確定。部分再走査は保存された対象の範囲内で行い、走査範囲外の既存一覧は維持する。初回は全対象を走査する。

ビルド出力先が対象ディレクトリの中にある場合は設定不整合として伝える案。配下をすべて対象にする方針を維持し、出力ファイルを黙って除外する方式にはしない。シンボリックリンク・Windowsのジャンクション配下を追跡する範囲は実装前に定める。

外部エディタ等で追加・移動されたファイルも認識するが、これをライブラリの`addFile`イベントとして通知しない。

<a id="dependencies"></a>

## ジャンル6：依存・リンク

### 最新の確認済み方針：link_projectと種類の指定

リンクの一回の呼び出しで指定する依存先プロジェクトは一つとし、一覧を渡す以前の案は採用しない。API名は単数形の `link_project` とする。第1引数で相手プロジェクトの設定用ディレクトリを指定し、第2引数でリンクする種類を指定する。相手の主ターゲットをリンク対象とし、利用する側は `target.settings` の所属Targetから決まる。

```python
target.settings.link_project(
    "../Math/.cppbuild",
    TargetType.STATIC_LIBRARY,
)
```

複数の相手をリンクするときは呼び出しを分ける。`link_target` への改名案と `link_projects` の複数形は、このAPIには採用しない。

各ターゲットは対応可能な種類を複数定義できる。例えば静的ライブラリ・共有ライブラリの両方に対応するターゲットに対し、利用側がリンク時に選択する。指定された種類への対応を検証し、非対応ならエラーにする。各種類に必要なビルド設定はターゲット側で保持する。種類を省略できるかと、その場合の既定値は未確定。従来の単一 `kind` のデータ表現はこの方針に合わせて見直す。

以下の複数依存先を一括指定する案とC++風宣言は旧案であり、この最新方針を優先する。

最新構成に合わせた確認用の案：リンクを利用する側の `Target` の設定として、`target.settings.link_projects(dependencies)`、`unlink(dependency_id)`、`link_package(package)`、`link_cmake_source(source)`、`link_imported_library(library)` を配置する。利用側ターゲットはオブジェクトから決まるので、従来の `consumer` 引数は不要となる。この配置変更は未確認。後半三つの外部ライブラリ対応は引き続き候補。

準拠プロジェクトは `.cppbuild` ディレクトリのパスを渡す案とし、内部でプロジェクトとターゲットの設定を読み込む。省略時に主ターゲットをリンク先に採用する案は、リンク可能な種類で外部公開されているかを検証する前提とする。主ターゲット以外を指定する経路も用意する案。従来の独立した `defaultLinkTarget` 設定を残すかは、この案と合わせて確認する。以下のC++風宣言・Project直下の操作は従来案。

担当クラスは`Project`。準拠プロジェクト間のリンクと解除、非準拠ライブラリの取り込み候補をまとめる。

### 準拠プロジェクト間のリンクと解除

```cpp
struct LinkOptions { Visibility visibility = Visibility::Private; };
struct LinkReport {
    std::vector<DependencyId> dependencies;
    std::vector<TargetName> linkedTargets;
    ChangeReport changes;
};

Result<LinkReport> linkProjects(
    TargetName consumer, std::vector<Path> dependencyConfigFiles, LinkOptions options = {});

struct ProjectDependency {
    Path configFile;                       // 依存先の共通設定ファイル
    std::vector<TargetName> targets;       // 空なら依存先の既定公開ターゲット
};
Result<LinkReport> linkProjects(
    TargetName consumer, std::vector<ProjectDependency> dependencies,
    LinkOptions options = {});

Result<ChangeReport> unlink(TargetName consumer, DependencyId dependency);
```

依存先の設定ファイルパス一覧だけの簡易版と、対象ターゲットも指定する詳細版を用意する案。可変個数は一覧で渡す方式を示し、可変長引数テンプレートを必須にしない。

準拠プロジェクトは指定された設定ファイルを読み、公開対象と必要情報を取得する。依存先の既定公開ターゲットがなく、公開対象も複数なら候補を返して選択を求める。設定ファイルのパスを直接指定するため、設定ファイルを探す規約は不要。

手元のソースを一緒にビルドする経路は`add_subdirectory`、接続は`target_link_libraries`を使う候補。呼出し時は依存設定を保存し、実際のCMake反映はupdate時に行う。重複登録は増やさず、循環等は診断する。`unlink`は接続設定を削除し、依存先のファイルを消さない。

### 非準拠ライブラリへの候補API（対応範囲は未採用）

```cpp
struct CMakePackage {
    std::string name;
    std::optional<std::string> version;
    std::vector<Path> searchPrefixes;
    std::vector<TargetName> targets;
};
struct CMakeSource {
    Path sourceDirectory;
    std::vector<TargetName> targets;
};
struct BinaryFiles {
    Path library;                         // 静的ライブラリ、またはDLL本体等
    std::optional<Path> importLibrary;     // Windows DLLのリンク用.lib等
};
struct ImportedLibrary {
    TargetName name;
    TargetKind kind;                       // ExecutableはこのAPIでは不可
    std::vector<Path> includeDirectories;
    std::vector<std::string> compileDefinitions;
    std::map<std::string, BinaryFiles> configurations; // Debug/Release等
};

Result<LinkReport> linkPackage(TargetName consumer, CMakePackage package, LinkOptions options = {});
Result<LinkReport> linkCMakeSource(TargetName consumer, CMakeSource source, LinkOptions options = {});
Result<LinkReport> linkImportedLibrary(TargetName consumer, ImportedLibrary library, LinkOptions options = {});
```

順に`find_package`、`add_subdirectory`、`IMPORTED`ターゲットを利用する候補。ヘッダーのみの場合はバイナリ指定不要。構成別バイナリの代替・対応付けや、追加の推移的依存情報は今後具体化する。単一パスから任意の外部ライブラリ設定を完全に推測することは約束しない。

`DependencySettings`は上記の依存種別、識別子、利用ターゲット、公開範囲を保存する型。settings().dependenciesから再読み込み後も解除対象を取得できるよう、少なくともid、consumer、visibility、依存先情報を公開する。リンク先ターゲットごとの接続を保持し、同じ接続の再登録は増やさず、公開範囲の変更として扱う。循環を一律禁止するのではなく、準拠プロジェクトの再帰的取り込み等、扱えない循環を診断する。CMakeが扱えるターゲット依存まで無条件に禁止しない。

<a id="build-run"></a>

## ジャンル7：ビルド・実行

担当クラスは`Project`。ビルド・再ビルド・クリーン・実行を扱う。

```cpp
struct BuildOptions {
    BuildSelection profile;
    std::string configuration;            // 空なら選択した構成の既定値
    std::vector<TargetName> targets;       // 空ならCMakeの通常の全体ビルド
    std::optional<unsigned> parallelJobs;
};
struct CleanOptions {
    BuildSelection profile;
    std::string configuration;
};
struct RunOptions {
    BuildSelection profile;
    std::string configuration;
    std::vector<std::string> arguments;
    std::optional<Path> workingDirectory;  // 省略時は設定ファイルの所在ディレクトリ
    std::map<std::string, std::string> environmentOverrides;
    bool buildBeforeRun = true;
};

Result<OperationReport> build(BuildOptions options = {}, ExecutionOptions execution = {});
Result<OperationReport> rebuild(BuildOptions options = {}, ExecutionOptions execution = {});
Result<OperationReport> clean(CleanOptions options = {}, ExecutionOptions execution = {});
Result<ProcessReport> run(TargetName executable, RunOptions options = {}, ExecutionOptions execution = {});
```

| API | 主な処理 |
| --- | --- |
| `build` | 対象ディレクトリの変更を確認し、必要ならupdate相当の処理をしてからCMake経由でビルドする。毎回CMakeを強制実行する意味ではない。 |
| `rebuild` | 必要な更新後、クリーンしてビルドする。途中失敗で後続を止める。ターゲット指定があってもクリーン範囲は生成器により広がり得るため、対象だけの消去は保証しない。 |
| `clean` | 対応するビルドツールのcleanをCMake経由で呼ぶ。ソース・共通設定・プロジェクト自体は削除しない。本案ではターゲット指定を設けず、ビルド全体のcleanとする。未生成なら実行不要として診断を返す。 |
| `run` | 指定ターゲットを必要ならビルドし、構成に対応した成果物パスを取得して実行する。ビルド失敗時はアプリを起動せずErrorに工程結果を含める。ライブラリターゲットなら失敗。 |

Visual Studio等の複数構成型ではビルド時のconfigurationを使い、単一構成型ではconfigure時にも反映する。生成器・ツールチェーンが既存のビルドディレクトリと不整合なら別ディレクトリの指定を求め、勝手に削除しない。成果物パスを固定の`Debug/MyApp.exe`等と仮定しない。

実行環境は親プロセスの環境にExecutionOptions.runtimeの上書きを適用する。RunOptions.environmentOverridesはその上からアプリ本体にだけ適用し、事前ビルドへは流用しない。一時指定でCMakeキャッシュが変わった後に別構成で同じビルドディレクトリを使う場合は、前回の有効構成との差を確認する。省略した値がキャッシュから消えるとは仮定せず、必要な再構成や不整合の診断を行う。

<a id="tests"></a>

## ジャンル8：テスト

担当クラスは`Project`。テスト構成の追加とテスト実行を扱う。

```cpp
struct TestSetupOptions {
    TargetName targetName;
    Path sourceDirectory;
    Path templateDirectory;
    Variables variables;
};
struct TestOptions {
    BuildSelection profile;
    std::string configuration;
    std::optional<std::string> namePattern;
    std::optional<unsigned> parallelJobs;
    bool buildBeforeTest = true;
};

Result<ChangeReport> addTests(TargetName subject, TestSetupOptions options);
enum class TestStatus { Passed, Failed, Skipped, NotRun };
struct TestCaseResult {
    std::string name;
    TestStatus status;
    std::optional<double> durationSeconds;
    std::string output;
};
struct TestReport {
    OperationReport execution;
    std::vector<TestCaseResult> cases;
    bool detailsAvailable;
};
Result<TestReport> test(TestOptions options = {}, ExecutionOptions execution = {});
```

この案では本体と同じプロジェクトにテスト用実行ファイルを追加する。addTestsのsubjectはリンク可能なライブラリターゲットとし、テンプレートからテスト構成を作り、本体とのリンクとCTest登録を設定する。通常の実行ファイルを別のテスト実行ファイルへそのままリンクする前提にはしない。実行ファイルのテストは、プロジェクトテンプレート側で本体ロジックをライブラリへ分けるか、CTestでアプリを実行する構成を用意する。CLIテスト用の汎用設定APIは必要性を確認してから追加する。

作成時のenableTestsは未指定ならテンプレートの既定値を保ち、指定された場合のみ上書きする。非対応テンプレートにtrueを指定した場合は明示的に失敗する。テンプレート内でテスト構成を用意してあるのに、APIの既定falseで無効化することを避ける。

`test`は既定で登録済みテストターゲットをビルドしてからCTestを実行する。パターンはCTestに渡すテスト名の正規表現。テスト失敗はexecution.succeeded=false。対象テストが0件なら本案では成功扱いにせず診断する。CTestの構造化した結果を内部で読み、CTest上のテスト単位で結果を返す。利用側にレポートファイルを作らせたりログ解析を求めたりしない。結果を取得できない場合はdetailsAvailable=falseと診断を返し、空一覧を「0件成功」と解釈させない。フレームワーク内部の全ケースまで自動的に分解することは約束しない。

テスト別プロジェクト方式を同時に必須対応とはしない。採用フレームワークと、同一プロジェクト方式の正式採用は未確定。

<a id="pch"></a>

## ジャンル9：PCH

担当クラスは`Project`。PCHの設定・解除と、まとめ用ヘッダーの作成支援を扱う。

```cpp
struct PchSettings {
    std::vector<Path> projectHeaders;      // プロジェクトルート基準
    std::vector<std::string> systemHeaders; // vector、string等
};

Result<ChangeReport> setPch(TargetName target, PchSettings settings);
Result<ChangeReport> clearPch(TargetName target);
Result<ChangeReport> createPchHeader(Path destination, PchSettings contents);
```

`setPch`はヘッダー一覧を設定に保存し、次回更新で`target_precompile_headers`へ反映する。`clearPch`は利用設定のみ外し、既存ヘッダーを消さない。`createPchHeader`は必要なincludeをまとめたヘッダーを作る補助機能で、自動的にPCHを有効にはしない。

PCHは対象の内部設定を既定とし、利用先へ押し付けない。コンパイルを行わないヘッダーのみターゲットへのsetPchはこの案では非対応として返す。

<a id="environment"></a>

## ジャンル10：環境チェック

担当クラスは`Environment`と`Project`。前者は単独の検査、後者は開いているプロジェクトの設定に応じた検査を扱う。

```cpp
struct ToolRequirement {
    std::string name;
    std::optional<std::string> minimumVersion;
    std::optional<Path> executable;
};
struct EnvironmentCheckOptions {
    RuntimeEnvironment runtime;
    std::vector<ToolRequirement> tools;
    std::vector<Path> searchDirectories;
    std::optional<std::string> generator;
};
struct EnvironmentReport {
    bool ready;
    std::map<std::string, Path> foundTools;
    std::vector<Diagnostic> diagnostics;
};

class Environment {
public:
    static Result<EnvironmentReport> check(EnvironmentCheckOptions options = {});
};
// Projectのメンバー。
Result<EnvironmentReport> checkEnvironment(
    BuildSelection profile = {}, EnvironmentCheckOptions options = {}) const;
```

単独のcheckはCMakeの存在等の基本チェックを行い、Project版は指定プロファイルに必要な項目を補う。省略時の基本項目は実装時に定める。見つかった場所、不足ツール、確認できなかった項目をデータで返す。不足は通常の診断結果ready=falseとし、検査処理自体が行えない場合をResultの失敗にする。

この検査だけでコンパイル成功を保証しない。実際のコンパイラ・SDK等の確認結果はCMakeのconfigure結果も使う。インストールや利用者への直接表示は行わない。

EnvironmentCheckOptions.runtimeとExecutionOptions.runtimeに同じ値を渡せば、検査と実行で同じツール指定・環境を使える。runtime内の実行ファイル指定とToolRequirement.executableが同じツールに異なるパスを指定した場合はエラーとする。searchDirectoriesで見つけたツールはfoundToolsとして返し、実行にも使う場合は利用側がruntimeにそのパスを設定する。検査結果を別のファイルに保存して実行APIへ渡す手順は不要。

<a id="events"></a>

## ジャンル11：イベント

担当クラスは`Project`。ファイル追加・ビルド・テストのコールバック登録と解除を扱う。

```cpp
enum class EventKind { FileAdded, BeforeBuild, AfterBuild, BeforeTest, AfterTest };
struct Event {
    EventKind kind;
    std::optional<Path> file;
    std::vector<TargetName> targets;
    std::optional<OperationReport> outcome;
};
using EventHandler = std::function<Result<void>(Project&, const Event&)>;
using HandlerId = /* 登録を識別する値 */;

Result<HandlerId> on(EventKind kind, EventHandler handler);
bool off(HandlerId handler);
```

`on`は実行中のProjectへ処理を登録し、解除用のIDを返す。`off`は登録を削除できたかを返す。コールバックは設定ファイルに保存せず、openし直した場合は利用側が登録する。外部コマンド化は利用側の責務。

例：FileAddedで渡されたパスを基にテスト側のパスを組み立て、`addFile`で対応するテストファイルを作る。対応規則はコールバックで決められ、ライブラリの共通規則に固定しない。

通知は登録順に同期実行。FileAddedは元ファイルの作成後に発生する。コールバック内から追加したファイルについては同じ通知を再帰発火させない案とし、無限のテスト生成を避ける。BeforeBuild等の失敗は後続を止める。AfterBuild等には工程失敗も含めた結果を渡し、既に行った処理は取り消さない。実行前コールバックがファイルや設定を変えた場合は、その変更を反映してからビルドを開始する。

run/test内部のビルドもビルドイベントを発生させるが、コールバックから同じビルド・テスト操作を再帰的に開始する呼出しは失敗とする。これらの通知規則は今回の設計案。

Visual Studio等から直接起動した外部ビルドは、このProject内のコールバックを呼ばない。外部コマンドをCMakeへ登録する専用APIは、現在の必須機能に追加しない。

## 付録A：元の機能要望との対応

| 要望 | 対応する主なAPI |
| --- | --- |
| 1 プロジェクト作成 | Project::create、TemplateTools |
| 2 リンク | linkProjects、外部ライブラリ向け候補API、unlink |
| 3 プロジェクト削除 | 対象外。専用APIを設けない |
| 4 ファイル操作 | addFile、removeFile、moveFile、createFileTemplate |
| 5 VSフィルター | setSourceDirectories、update |
| 6 更新 | update（走査は内部処理） |
| 7 テスト | createのenableTests、addTests、test |
| 8 ファイル追加連動 | on(FileAdded)、off |
| 9 ビルド・実行 | build、rebuild、clean、run |
| 10 環境チェック | Environment::check、checkEnvironment |
| 11 設定変更 | saveSettings、setTargetSettings、setBuildProfile等 |
| 12 PCH | setPch、clearPch、createPchHeader |
| 13 イベント | on、off |
| 14 保存・読み込み・情報取得 | open、saveSettings、reload、settings、info |

## 付録B：APIにして見えた、判断が必要な点

1. **設定保存のタイミング。** この案は変更APIで即保存する。まとめて変更してからsaveする案と比べ、利用側の手順は少ないが複数設定の一括変更はsaveSettingsを使う。未決定だったため今回の提案である。
2. **対象ディレクトリとターゲット。** すべての配下ファイルを表示する方針はそのまま実現できる。本体とテストを別々にビルドするには、どのターゲットの対象かを一度指定する必要があるため、ターゲットごとのsourceDirectoriesを提案した。
3. **更新の範囲。** updateは内部で配置を認識し、CMake生成とVSフィルター更新まで行う。独立した走査APIは設けない。構成は値で直接指定できるため事前保存は不要。
4. **リンク対象の選択。** openもlinkProjectsも設定ファイルのパスを入口とするため、設定ファイルの探索規約は不要になった。依存先の公開ターゲットが複数ある場合の既定値・選択方法は検討が必要。
5. **テスト構成。** 同一プロジェクトにテスト用ターゲットを置く案で具体化した。フレームワークやテンプレートの標準セットは別途決定する。
6. **同じファイルの二重コンパイル。** 本体とテストの対象ディレクトリを重ねると同じソースが両方へ入る可能性がある。通常のテンプレートではディレクトリを分け、本体はリンクして使う。重なりをエラーにするかは未決定。
7. **汎用化の範囲。** コールバックの再登録は利用側に委ねられる。現時点で独自イベント保存形式・コマンド化・CIシステムを内蔵する必要は見つからない。
8. **低水準オプションの範囲。** 任意のVSプロパティやCMake機能をすべて構造体に追加することは約束しない。利用頻度の高い設定を先に用意し、不足が分かってから拡張する。

## 付録C：CMakeとの対応を確認した公式資料

- configure/generateとbuildの段階、生成器・構成・ビルドディレクトリの扱い：[cmake CLI](https://cmake.org/cmake/help/latest/manual/cmake.1.html)。この資料ではその操作をupdate/build等へ割り当てる案とした。
- テスト実行・構成選択・名前による絞り込み：[CTest](https://cmake.org/cmake/help/latest/manual/ctest.1.html)。テストの初期テンプレートやライブラリAPIの形は本案独自。
- 依存の取り込みとターゲットによる利用：[Using Dependencies Guide](https://cmake.org/cmake/help/latest/guide/using-dependencies/index.html)。共通設定ファイルのパスから依存情報を読み込むAPIは本ライブラリの設計。
- PCHの設定と利用先へ強制しない方針：[target_precompile_headers](https://cmake.org/cmake/help/latest/command/target_precompile_headers.html)。
- 配置に沿ったVS表示：[source_group](https://cmake.org/cmake/help/latest/command/source_group.html)。

公式資料の機能と、今回提案したAPIの保存・自動更新・イベント等の方針は区別している。実装やビルドによる検証はまだ行っていない。
