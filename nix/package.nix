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
    propagatedBuildInputs = with pkgs.python3Packages; [ gtts-token aiohttp pyjwt cryptography ];
    doCheck = false;
  };

  # Custom Python dependency: davey (DAVE protocol E2EE)
  davey = pkgs.python3Packages.buildPythonPackage rec {
    pname = "davey";
    version = "0.1.6";
    format = "wheel";
    src = pkgs.python3Packages.fetchPypi rec {
      inherit pname version format;
      dist = "cp313";
      python = "cp313";
      abi = "cp313";
      platform = "manylinux_2_17_x86_64.manylinux2014_x86_64";
      sha256 = "b2bf56e88588c4e00690b9e5f81b09121855a338349ada0d2899c08270159cf3";
    };
    doCheck = false;
  };

  # Discord.py 2.7.1 with DAVE protocol support
  discordpy-dave = pkgs.python3Packages.buildPythonPackage rec {
    pname = "discord.py";
    version = "2.7.1";
    pyproject = true;
    build-system = [ pkgs.python3Packages.setuptools ];
    src = pkgs.python3Packages.fetchPypi {
      pname = "discord_py";
      version = "2.7.1";
      sha256 = "24d5e6a45535152e4b98148a9dd6b550d25dc2c9fb41b6d670319411641249da";
    };
    propagatedBuildInputs = with pkgs.python3Packages; [
      aiohttp
      pynacl
      davey
      audioop-lts
    ];
    doCheck = false;
  };

  python = pkgs.python3.override {
    packageOverrides = self: super: {
      inherit davey;
      discordpy = discordpy-dave;
    };
  };

  # Python environment with all required dependencies
  pythonEnv = python.withPackages (ps: with ps; [
    # Standard dependencies from requirements.txt
    speechrecognition
    openai-whisper
    pydub
    soundfile
    discordpy
    davey
    audioop-lts
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
    cryptography

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
      --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg ]} \
      --prefix LD_LIBRARY_PATH : ${pkgs.lib.makeLibraryPath [ pkgs.libopus ]}

    # Link alembic script to bin so migrations can be run easily
    ln -s ${pythonEnv}/bin/alembic $out/bin/alembic
  '';

  passthru = {
    inherit pythonEnv;
  };
}
