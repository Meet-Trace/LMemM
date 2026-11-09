// Synthetic scenarios share one note throughout the page; no visitor data is used.
window.TraceScenarios = {
 building: {
  project:'Pricing launch', note:'Pricing notes', noteTitle:'Keep the free tier.<br>Give people room.', quote:'“Keep the free tier.<br>Give people room to try it.”',
  retained:'“Keep the free tier so people can try it. Explore usage-based pricing for team features. Next: validate the pricing proposal.”',
  task:'Validate the pricing proposal', shortNote:'“Keep the free tier…”', resume:'Free stays free.<br><em>Teams pay by usage.</em>',
  description:'A pricing launch. A decision worth keeping.', sourceDescription:'From the pricing notes you retained.',
  decision:{label:'THE DECISION',answer:'Keep the free tier.<br><em>Usage-based pricing for teams.</em>',description:'The same decision from the note you followed through the page.'},
  source:{label:'THE REASONING',answer:'“Give people room to try it.”<br><em>The thinking behind the choice.</em>',description:'Open Pricing notes to read the retained evidence.'},
  next:{label:'THE NEXT STEP',answer:'Validate the pricing proposal.<br><em>Then pick up from there.</em>',description:'The next action recorded in the same project notes.'}
 },
 studying: {
  project:'Cities & green spaces',note:'Research notes',noteTitle:'Focus on access.<br>Compare two cities.',quote:'“Compare access to green space,<br>not just total park area.”',
  retained:'“Focus the paper on access to green space rather than total park area. Total area misses who can use it. Compare two cities so the scope stays manageable. Next: compare the two city datasets.”',
  task:'Compare the two city datasets',shortNote:'“Focus on access…”',resume:'Access, not just area.<br><em>Two cities. One clear question.</em>',
  description:'A research paper. The thread behind your argument.',sourceDescription:'From the research notes you retained.',
  decision:{label:'THE RESEARCH DIRECTION',answer:'Compare access to green space.<br><em>Keep the scope to two cities.</em>',description:'The focus you recorded, before another week of reading.'},
  source:{label:'THE REASONING',answer:'“Total area misses who can use it.”<br><em>Go back to the research notes.</em>',description:'Your retained note keeps the scope and next step together.'},
  next:{label:'THE NEXT STEP',answer:'Compare the two city datasets.<br><em>Build on the question you chose.</em>',description:'Continue the research instead of rediscovering your plan.'}
 },
 everyday: {
  project:'A week in Lisbon',note:'Trip notes',noteTitle:'Stay near the metro.<br>Leave room to wander.',quote:'“Stay near the metro.<br>Keep one day unplanned.”',
  retained:'“Skip the car. Keep it flexible. Choose somewhere near the metro instead of renting a car. Keep one day unplanned so we can wander. Next: compare the two places on our shortlist.”',
  task:'Compare the two shortlisted places',shortNote:'“Stay near the metro…”',resume:'Near the metro.<br><em>One day left open.</em>',
  description:'A trip taking shape. All the little choices, connected.',sourceDescription:'From the trip notes you retained.',
  decision:{label:'THE PLAN',answer:'Stay near the metro.<br><em>Leave one day for wandering.</em>',description:'The choices you made while the trip was taking shape.'},
  source:{label:'THE REASONING',answer:'“Skip the car. Keep it flexible.”<br><em>It’s right there in Trip notes.</em>',description:'Return to your retained plan without reopening every tab.'},
  next:{label:'THE NEXT STEP',answer:'Compare the two shortlisted places.<br><em>Pick up the fun part.</em>',description:'The next thing on your list, with the plan behind it.'}
 }
};
