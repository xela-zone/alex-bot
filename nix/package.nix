{ pkgs }:

let
  # Custom Python dependency: geomag (from custom fork)
  geomag = pkgs.python3Packages.buildPythonPackage rec {
    pname = "geomag";
    version = "1.0.0";
    pyproject = true;
    build-system = [ pkgs.python3Packages.setuptools ];
    src = pkgs.fetchFromGitHub {
      owner = "mralext20";
      repo = "geomag";
      rev = "57c2f214a209d1eb1022d022c6b2cf693a2305c4";
      sha256 = "sha256-xsLPA8uwBiG4u4evvL0lmHCDG2xNQ/05vOdfcxbEuIk=";
    };
    doCheck = false;
  };

  # Custom Python dependency: emoji-data
  emoji-data = pkgs.python3Packages.buildPythonPackage rec {
    pname = "emoji-data";
    version = "0.5.0";
    pyproject = true;
    build-system = [
      pkgs.python3Packages.setuptools
      pkgs.python3Packages.setuptools-scm
    ];
    src = pkgs.python3Packages.fetchPypi {
      pname = "emoji_data";
      version = "0.5.0";
      sha256 = "0f05br6b7ymcymxryk21fwxpzk4kpz370m0kprhhlp4x26pjy6sz";
    };
    doCheck = false;
  };

  # Custom Python dependency: async-gTTS
  async-gtts = pkgs.python3Packages.buildPythonPackage rec {
    pname = "async-gTTS";
    version = "0.3.0";
    pyproject = true;
    build-system = [ pkgs.python3Packages.setuptools ];
    src = pkgs.python3Packages.fetchPypi {
      pname = "async-gTTS";
      version = "0.3.0";
      sha256 = "031kh4kr7nyw9jl1h98svp75np5y84g8b5ym8bf4y7jbamirl4sc";
    };
    propagatedBuildInputs = with pkgs.python3Packages; [ gtts-token aiohttp pyjwt ];
    doCheck = false;
  };

  # Python environment with all required dependencies
  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    # Standard dependencies from requirements.txt
    speechrecognition
    openai-whisper
    pydub
    soundfile
    discordpy
    jishaku
    aiohttp
    chardet
    multidict
    urllib3
    humanize
    python-slugify
    mcstatus
    avwx-engine
    xmltodict
    pytz
    httpx
    feedparser
    aiomqtt
    sqlalchemy
    alembic
    psycopg2
    asyncpg
    python-dotenv

    # Custom dependencies packaged above
    geomag
    emoji-data
    async-gtts
  ]);
in
pkgs.stdenv.mkDerivation {
  pname = "alex-bot";
  version = "2.3.1";

  src = ./..;

  nativeBuildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    mkdir -p $out/share/alex-bot
    cp -r bot.py config.py alexBot alembic alembic.ini $out/share/alex-bot/

    mkdir -p $out/bin
    # Wrap bot.py
    makeWrapper ${pythonEnv}/bin/python $out/bin/alex-bot \
      --add-flags "$out/share/alex-bot/bot.py" \
      --set PYTHONPATH "$out/share/alex-bot" \
      --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg ]}

    # Link alembic script to bin so migrations can be run easily
    ln -s ${pythonEnv}/bin/alembic $out/bin/alembic
  '';

  passthru = {
    inherit pythonEnv;
  };
}
