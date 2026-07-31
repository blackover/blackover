/**
 * Tarayıcı testi: müşteri değiştirince listeler tazeleniyor mu?
 *
 * Uygulama birden çok lisans (müşteri) için sırayla kullanılıyor. Sayfayı
 * yenilemeden çıkış yapıp başka bir müşteriyle girildiğinde, bir önceki
 * müşterinin tank listesi ekranda kalmamalı — kalırsa hem yanlış tanklar
 * görünür hem de tank doğrulaması yanlış listeye bakar.
 *
 * Bu betik Python test paketinden çağrılır; doğrudan da çalıştırılabilir:
 *
 *   APP_URL=http://127.0.0.1:8787 MOCK_URL=http://127.0.0.1:9000/petrolstok/api \
 *   node tests/browser_customer_switch.js
 *
 * Çıkış kodu 0 = geçti, 2 = kaldı.
 */

const APP = process.env.APP_URL || 'http://127.0.0.1:8787';
const MOCK = process.env.MOCK_URL || 'http://127.0.0.1:9000/petrolstok/api';
const BROWSER = process.env.CHROMIUM_PATH || undefined;
const USER_A = process.env.USER_A || 'WSU-DEP/444-2/01592';
const USER_B = process.env.USER_B || 'WSU-DEP/7646-3/39543';
const PASSWORD = process.env.EPDK_TEST_PASSWORD || 'deneme';

async function main() {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch {
    ({ chromium } = require(process.env.PLAYWRIGHT_PATH
      || '/opt/node22/lib/node_modules/playwright'));
  }

  const browser = await chromium.launch(BROWSER ? { executablePath: BROWSER } : {});
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  const signIn = async (username) => {
    await page.click('[data-env="custom"]');
    await page.fill('#custom-url', MOCK);
    await page.fill('#username', username);
    await page.fill('#password', PASSWORD);
    await page.click('#login-submit');
    await page.waitForSelector('#app:not([hidden])', { timeout: 20000 });
    await page.waitForTimeout(2200);
  };

  const slotChips = () => page.$$eval('.slot-chip',
    (nodes) => nodes.map((node) => node.textContent.trim().replace(/^[✓+]\s*/, '')));

  await page.goto(APP, { waitUntil: 'networkidle' });
  await page.waitForTimeout(700);
  if (!(await page.locator('#app').isHidden())) {
    await page.click('#logout-btn');
    await page.waitForTimeout(900);
  }

  await signIn(USER_A);
  const tanksA = await slotChips();

  // Sayfayı yenilemeden müşteri değiştir — hatanın ortaya çıktığı yol budur.
  await page.click('#logout-btn');
  await page.waitForTimeout(900);
  await signIn(USER_B);
  const tanksB = await slotChips();

  // Form içindeki tank listesi de yeni müşteriye ait olmalı
  await page.click('[data-route="dep1"]');
  await page.waitForTimeout(1500);
  await page.click('[data-act="new"]');
  await page.waitForSelector('.modal', { timeout: 10000 });
  await page.waitForTimeout(600);
  const formTanks = await page.$$eval('#dl-tankNumarasi option',
    (nodes) => nodes.map((node) => node.value));

  await browser.close();

  const overlap = tanksA.filter((tank) => tanksB.includes(tank));
  const problems = [];
  if (!tanksA.length) problems.push('ilk müşteri için tank çipi bulunamadı');
  if (!tanksB.length) problems.push('ikinci müşteri için tank çipi bulunamadı');
  if (overlap.length) {
    problems.push(`önceki müşterinin tankları kaldı: ${overlap.join(', ')}`);
  }
  if (formTanks.some((tank) => tanksA.includes(tank) && !tanksB.includes(tank))) {
    problems.push('form tank listesi önceki müşteriden geliyor');
  }
  if (pageErrors.length) problems.push(`sayfa hatası: ${pageErrors.join(' | ')}`);

  console.log(JSON.stringify({
    customerA: tanksA, customerB: tanksB, formTanks, problems,
  }, null, 1));

  if (problems.length) {
    console.error('KALDI: ' + problems.join(' · '));
    process.exit(2);
  }
  console.log('GEÇTİ: müşteri değişince tank listeleri tazelendi.');
}

main().catch((error) => {
  console.error('HATA:', error.message);
  process.exit(1);
});
