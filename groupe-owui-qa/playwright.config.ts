/**
 * Configuration Playwright du projet F1.
 * Inspirée de la suite CoursIA Playwright-OWUI (MIT).
 */
import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Charge OWUI_URL, OWUI_EMAIL, OWUI_PASSWORD depuis .env
dotenv.config({ path: path.resolve(__dirname, '.env'), quiet: true });

export const AUTH_FILE = path.resolve(__dirname, '.auth/admin.json');

export default defineConfig({
  testDir: './e2e',
  // Un seul worker : les tests partagent la même instance Open WebUI.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  timeout: 60_000,
  expect: { timeout: 10_000 },

  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: process.env.OWUI_URL || 'http://localhost:3000',
    locale: 'fr-FR',
    // Preuves en cas d'échec (exigence du MVP : traces / screenshots).
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },

  projects: [
    // 1. Connexion unique, session sauvegardée dans .auth/ (ignoré par Git)
    {
      name: 'setup',
      testMatch: /fixtures\/.*\.setup\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    // 2. Les tests, qui réutilisent la session
    {
      name: 'owui',
      testMatch: /tests\/.*\.spec\.ts/,
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], storageState: AUTH_FILE },
    },
  ],
});
