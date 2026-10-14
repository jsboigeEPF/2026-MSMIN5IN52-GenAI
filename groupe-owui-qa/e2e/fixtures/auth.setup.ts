/**
 * Se connecte UNE fois via l'interface et sauvegarde la session (cookies +
 * localStorage) : les tests suivants démarrent déjà connectés.
 * Évite aussi la limite de débit d'Open WebUI sur /api/v1/auths/signin.
 */
import { test as setup, expect } from '@playwright/test';
import { AUTH_FILE } from '../../playwright.config';
import { AUTH, CHAT } from '../helpers/selectors';

setup('connexion admin', async ({ page }) => {
  const email = process.env.OWUI_EMAIL;
  const password = process.env.OWUI_PASSWORD;
  if (!email || !password) {
    throw new Error('OWUI_EMAIL / OWUI_PASSWORD manquants : copier .env.example vers .env et les renseigner.');
  }

  await page.goto('/auth');
  await page.locator(AUTH.emailInput).fill(email);
  await page.locator(AUTH.passwordInput).fill(password);
  await page.locator(AUTH.submitButton).click();

  // Connecté = on a quitté /auth et la zone de saisie du chat est affichée.
  await page.waitForURL((url) => !url.pathname.startsWith('/auth'), { timeout: 30_000 });
  await expect(page.locator(CHAT.input)).toBeVisible({ timeout: 30_000 });

  await page.context().storageState({ path: AUTH_FILE });
});
