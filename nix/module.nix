{ self }:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.alex-bot;
in
{
  options.services.alex-bot = {
    enable = lib.mkEnableOption "alex-bot Discord Bot";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.system}.default;
      description = "The alex-bot package to use.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Path to environment file containing secrets like DISCORD_TOKEN, MQTT_URL, etc.";
    };

    gcpServiceAccountFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Path to GOOGLE_SERVICE_ACCOUNT.json GCP credentials file.";
    };

    databaseUrl = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Plaintext DATABASE_URL connection string. WARNING: This will be stored in the world-readable Nix store; do not use this if your database URL contains passwords. Use databaseUrlFile instead.";
    };

    databaseUrlFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Path to file containing DATABASE_URL connection string (useful for secrets-management tools).";
    };

    botPrefix = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Bot prefix.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.alex-bot = {
      description = "Alex-bot Discord Bot Service";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = let
        dbUrlFile =
          if cfg.databaseUrlFile != null then
            cfg.databaseUrlFile
          else if cfg.databaseUrl != null then
            pkgs.writeText "alex-bot-db-url" cfg.databaseUrl
          else
            null;
      in {
        Type = "simple";
        DynamicUser = true;
        RuntimeDirectory = "alex-bot";
        WorkingDirectory = "/run/alex-bot";
        EnvironmentFile = lib.optional (cfg.environmentFile != null) cfg.environmentFile;

        # Inline systemd environment variables for static configurations
        Environment =
          lib.optional (cfg.botPrefix != null) "BOT_PREFIX=${cfg.botPrefix}";

        # Pass secret files securely to the dynamic user via systemd credentials
        LoadCredential =
          (lib.optional (dbUrlFile != null) "database_url:${dbUrlFile}")
          ++ (lib.optional (cfg.gcpServiceAccountFile != null) "gcp_json:${cfg.gcpServiceAccountFile}");

        # Setup configs/credentials and execute Alembic migrations before starting
        ExecStartPre = pkgs.writeShellScript "alex-bot-setup" ''
          # Link alembic and package resources into the temporary runtime directory
          ln -sf ${cfg.package}/share/alex-bot/alembic.ini /run/alex-bot/alembic.ini
          ln -sfT ${cfg.package}/share/alex-bot/alembic /run/alex-bot/alembic
          ln -sfT ${cfg.package}/share/alex-bot/alexBot /run/alex-bot/alexBot

          # Symlink the google service account json if provided
          if [ -f "$CREDENTIALS_DIRECTORY/gcp_json" ]; then
            ln -sf "$CREDENTIALS_DIRECTORY/gcp_json" /run/alex-bot/GOOGLE_SERVICE_ACCOUNT.json
          else
            rm -f /run/alex-bot/GOOGLE_SERVICE_ACCOUNT.json
          fi

          # Set DATABASE_URL from databaseUrlFile if provided
          if [ -f "$CREDENTIALS_DIRECTORY/database_url" ]; then
            export DATABASE_URL=$(cat "$CREDENTIALS_DIRECTORY/database_url")
          fi

          # Run migrations
          ${cfg.package}/bin/alembic upgrade head
        '';

        ExecStart = pkgs.writeShellScript "alex-bot-start" ''
          # Set DATABASE_URL from databaseUrlFile if provided
          if [ -f "$CREDENTIALS_DIRECTORY/database_url" ]; then
            export DATABASE_URL=$(cat "$CREDENTIALS_DIRECTORY/database_url")
          fi

          exec ${cfg.package}/bin/alex-bot
        '';

        Restart = "always";
        RestartSec = 10;
      };
    };
  };
}
