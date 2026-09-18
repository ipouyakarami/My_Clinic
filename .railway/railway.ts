import { defineRailway, postgres, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const Postgres = postgres("Postgres", { region: "sfo" });
  Postgres.networking = { privateNetworkEndpoint: "postgres" };
  const webVolume = volume("web-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "sfo", sizeMB: 500 });
  const postgresVolume = volume("postgres-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "sfo", sizeMB: 500 });
  const beat = service("beat", {
    start: "python manage.py run_telegram_bot",
    replicas: { "sfo": 1 },
    env: { DATABASE_URL: preserve(), DEFAULT_FROM_EMAIL: preserve(), DEMO_MODE: preserve(), DJANGO_DEBUG: preserve(), DJANGO_SECRET_KEY: preserve(), EMAIL_HOST_PASSWORD: preserve(), EMAIL_HOST_USER: preserve(), SERVICE_ROLE: preserve(), SITE_URL: preserve(), TELEGRAM_BOT_TOKEN: preserve(), TELEGRAM_BOT_USERNAME: preserve(), TIME_ZONE: preserve() },
  });
  const web = service("web", {
    replicas: { "sfo": 1 },
    deploy: { ipv6EgressEnabled: true },
    volumeMounts: { "/app/media": webVolume },
    env: { DATABASE_URL: preserve(), DEFAULT_FROM_EMAIL: preserve(), DEMO_MODE: preserve(), DEMO_PASSWORD: preserve(), DJANGO_DEBUG: preserve(), DJANGO_SECRET_KEY: preserve(), EMAIL_HOST_PASSWORD: preserve(), EMAIL_HOST_USER: preserve(), TELEGRAM_BOT_TOKEN: preserve(), TELEGRAM_BOT_USERNAME: preserve(), TIME_ZONE: preserve() },
  });
  const worker = service("worker", {
    start: "celery -A myclinic worker -B --concurrency 2 -l info",
    replicas: { "sfo": 1 },
    deploy: { ipv6EgressEnabled: true },
    env: { DATABASE_URL: preserve(), DEFAULT_FROM_EMAIL: preserve(), DEMO_MODE: preserve(), DJANGO_DEBUG: preserve(), DJANGO_SECRET_KEY: preserve(), EMAIL_HOST_PASSWORD: preserve(), EMAIL_HOST_USER: preserve(), SERVICE_ROLE: preserve(), TELEGRAM_BOT_TOKEN: preserve(), TELEGRAM_BOT_USERNAME: preserve(), TIME_ZONE: preserve() },
  });

  return project("myclinic", {
    resources: [beat, Postgres, web, worker, webVolume, postgresVolume],
  });
});
