import { test, expect } from '@playwright/test';
import { CHAT } from '../helpers/selectors';

test.describe('Connexion', () => {
  test('un utilisateur connecté arrive sur la page de chat', async ({ page }) => {
    await page.goto('/');

    // Pas de redirection vers la page de login : la session a été reprise.
    await expect(page).not.toHaveURL(/\/auth/);
    await expect(page.locator(CHAT.input)).toBeVisible();
  });
});
