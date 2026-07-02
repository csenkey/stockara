/**
 * Creates a Google Form for Stockara stock-expert feedback.
 *
 * How to use:
 * 1. Open https://script.google.com/ in the Google account that should own the form.
 * 2. Create a new Apps Script project.
 * 3. Paste this file into Code.gs.
 * 4. Run createStockaraExpertReviewForm().
 * 5. Authorize the script when prompted.
 * 6. Open the logged "Published form URL" to share the form.
 */

function createStockaraExpertReviewForm() {
  const form = FormApp.create('Stockara Expert Review Questionnaire');
  form.setDescription(
    'Structured feedback form for stock-market experts reviewing Stockara data inputs, data gates, scoring, AI analysis, AI review, and web UI output.'
  );
  form.setCollectEmail(true);
  form.setAllowResponseEdits(true);
  form.setConfirmationMessage(
    'Thank you for reviewing Stockara. Your feedback will be used to improve the recommendation workflow.'
  );

  addReviewerBackground_(form);
  addDataCollection_(form);
  addDataGates_(form);
  addCandidateScoring_(form);
  addAiAnalysis_(form);
  addAiReview_(form);
  addWebUiOutput_(form);
  addOverallAssessment_(form);

  Logger.log('Edit URL: ' + form.getEditUrl());
  Logger.log('Published form URL: ' + form.getPublishedUrl());
}

function addReviewerBackground_(form) {
  form.addPageBreakItem().setTitle('Reviewer Background');
  form.addTextItem().setTitle('Name').setRequired(true);
  form.addTextItem().setTitle('Role or area of expertise').setRequired(true);
  form.addTextItem().setTitle('Years of market/investment experience');
  form.addParagraphTextItem().setTitle('Main asset classes or sectors reviewed');
}

function addDataCollection_(form) {
  form.addPageBreakItem().setTitle('Data Collection');
  addScale_(
    form,
    'The current data categories are sufficient for a daily catalyst scanner.'
  );
  addScale_(
    form,
    'Daily pre-publication news collection is sufficient for the current Stockara workflow.'
  );
  addScale_(
    form,
    'Earnings, dividends, SEC filings, analyst actions, sector context, and macro proxies provide enough event context for first-stage review.'
  );
  form.addParagraphTextItem()
    .setTitle('What missing sources or evidence types should Stockara consider essential?');
  form.addParagraphTextItem()
    .setTitle('Which current sources or evidence types are least useful or potentially noisy?');
  form.addParagraphTextItem()
    .setTitle('What collection schedule would you recommend for price, news, earnings, dividends, filings, analyst actions, sector context, and macro context?');
}

function addDataGates_(form) {
  form.addPageBreakItem().setTitle('Data Gates');
  addScale_(form, 'A maximum 3-day age for latest price data is appropriate.');
  addScale_(
    form,
    'At least 30 calendar days and 20 price rows is enough for a decision-grade near-term scan.'
  );
  addScale_(
    form,
    'Stale news should be shown as a warning rather than automatically suppressing every stock.'
  );
  form.addParagraphTextItem()
    .setTitle('Should different stock types have different freshness or history requirements?');
  form.addParagraphTextItem()
    .setTitle('What minimum history and freshness requirements would you recommend?');
  form.addParagraphTextItem()
    .setTitle('Under what conditions should stale or missing news suppress publication?');
}

function addCandidateScoring_(form) {
  form.addPageBreakItem().setTitle('Candidate Scoring');
  addScale_(
    form,
    'Separating opportunity score and negative score is the right high-level scoring structure.'
  );
  addScale_(form, 'Sell alerts should use different thresholds than BUY opportunities.');
  addScale_(form, 'Sector-relative strength should affect candidate scoring.');
  addScale_(form, 'Company size should affect score interpretation.');
  form.addParagraphTextItem().setTitle('Which signals should carry more weight?');
  form.addParagraphTextItem().setTitle('Which signals should carry less weight?');
  form.addParagraphTextItem().setTitle('What scoring thresholds would you recommend for BUY candidates?');
  form.addParagraphTextItem().setTitle('What scoring thresholds would you recommend for SELL alerts?');
  form.addParagraphTextItem().setTitle('What examples would you use to calibrate scoring quality?');
}

function addAiAnalysis_(form) {
  form.addPageBreakItem().setTitle('AI Analysis');
  addScale_(
    form,
    'The AI analyst receives the right evidence for a useful near-term assessment.'
  );
  addScale_(
    form,
    'The AI should be required to mention upcoming earnings or dividends when relevant.'
  );
  addScale_(
    form,
    'The requested outputs are clear: recommendation, risk, confidence, catalyst, timeframe, reasoning, and invalidation criteria.'
  );
  form.addParagraphTextItem()
    .setTitle('Should the AI prompt require valuation, liquidity, balance-sheet, volatility, or other context?');
  form.addParagraphTextItem().setTitle('What evidence should the AI never ignore?');
  form.addParagraphTextItem().setTitle('What would make the AI reasoning more useful to a stock expert?');
  form.addParagraphTextItem().setTitle('What mistakes would you expect the AI analyst to make?');
}

function addAiReview_(form) {
  form.addPageBreakItem().setTitle('AI Review');
  addScale_(
    form,
    'The second AI review step is necessary before publishing BUY or SELL recommendations.'
  );
  addScale_(form, 'The review step should be stricter than the first AI analyst step.');
  addScale_(form, 'Rejected AI ideas should remain visible to expert reviewers.');
  form.addParagraphTextItem().setTitle('What rejection categories would be most useful for expert audit?');
  form.addParagraphTextItem().setTitle('Should confidence adjustments be constrained differently?');
  form.addParagraphTextItem().setTitle('Should rejected ideas be hidden from non-expert users?');
  form.addParagraphTextItem().setTitle('What evidence standard should a recommendation meet before publication?');
}

function addWebUiOutput_(form) {
  form.addPageBreakItem().setTitle('Web UI Output');
  addScale_(form, 'The Top Picks card shows enough information to judge a recommendation quickly.');
  addScale_(form, 'The Sell Alert card shows enough negative evidence and urgency.');
  addScale_(form, 'Data warnings are prominent enough.');
  addScale_(form, 'The Withheld AI Recommendations section is useful for expert review.');
  addScale_(form, 'The static charts are sufficient for first-pass review.');
  form.addParagraphTextItem().setTitle('What additional chart indicators should be shown?');
  form.addParagraphTextItem().setTitle('What information is missing from Top Picks?');
  form.addParagraphTextItem().setTitle('What information is missing from Sell Alerts?');
  form.addParagraphTextItem().setTitle('What would make the GUI easier for expert review sessions?');
}

function addOverallAssessment_(form) {
  form.addPageBreakItem().setTitle('Overall Assessment');
  addScale_(
    form,
    "Stockara's current workflow is a reasonable foundation for expert-reviewed daily stock recommendations."
  );
  addScale_(form, "Stockara's current warnings are understandable and actionable.");
  addScale_(
    form,
    "Stockara's current publication controls reduce the risk of unsupported recommendations."
  );
  form.addParagraphTextItem().setTitle("What would make Stockara's published recommendations more trustworthy?");
  form.addParagraphTextItem().setTitle("What would make Stockara's warnings more actionable?");
  form.addParagraphTextItem()
    .setTitle('Which recommendation examples should be reviewed manually to calibrate scoring and review strictness?');
  form.addParagraphTextItem()
    .setTitle('What minimum evidence standard should a public BUY or SELL recommendation meet?');
  form.addParagraphTextItem().setTitle('Any other comments or concerns?');
}

function addScale_(form, title) {
  form.addScaleItem()
    .setTitle(title)
    .setBounds(1, 5)
    .setLabels('Strongly disagree / not sufficient', 'Strongly agree / sufficient')
    .setRequired(true);
}
