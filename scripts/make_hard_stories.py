#!/usr/bin/env python3
"""Write the round-5 teaching stories: the hard cases the judge got wrong on the invented test days. Research only.

    python3 scripts/make_hard_stories.py            # writes research/judge_lessons/stories_hard.json

The judge's own mistakes on the trail days were look-alikes filed as one project, and a project that changes
topic split in two. So these stories come in two kinds, all invented and none from anyone's data:

    twins   eight pairs of stories that look alike on the surface and are different projects: one repository
            with two branches, one website with two jobs, one file name in two folders, one comparison site for
            two purchases, one colleague in two projects. Each story names its twin ("twins"), so the lesson
            builder draws most of its "no" answers from the twin, and keeps both in the same split.
    pivots  six stories whose project changes topic half-way, with one or two bridge windows that carry both
            topics (a note, a mail, a plan). Each window says which part it is ("phase": early, bridge, late).
            The judge learns that early and late windows, which share no words, are one project.

Nothing here uses the words of the test days (make_judge_lessons.EXAM_WORDS refuses them), and a file name
that is a test day's is not reused.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OUT = ROOT / "research" / "judge_lessons" / "stories_hard.json"


def w(app, title, details, lines, did, phase=None):
    out = {"app": app, "title": title, "details": details, "lines": lines, "did": did}
    if phase:
        out["phase"] = phase
    return out


def inbox(n, *subjects):
    return w("Gmail", f"Inbox ({n})", {}, list(subjects), "Looking at an email inbox.")


def story(sid, theme, windows, general=(), twin=None, pivot=False):
    out = {"id": sid, "theme": theme, "round": 5}
    if twin:
        out["twins"] = [twin]
    if pivot:
        out["pivot"] = True
    out["windows"] = windows
    out["general"] = list(general)
    return out


def url(address):
    return {"url": address}


def file(path):
    return {"file": path}


def cwd(path):
    return {"cwd": path}


def twins():
    """Eight pairs: the same kind of work, the same places, two different projects."""
    rb = "~/code/recipe-box"
    yield story("rb-meal-planner", "building a weekly meal planner screen for a recipe app", [
        w("Terminal", "recipe-box (feat/meal-planner) - zsh", cwd(rb),
          ["$ git checkout -b feat/meal-planner", "Switched to a new branch 'feat/meal-planner'",
           "$ pytest tests/test_planner.py", "2 failed, 5 passed"],
          "Starting a new branch for the meal planner and running its tests."),
        w("Visual Studio Code", "planner.py - recipe-box", file(f"{rb}/app/planner.py"),
          ["def build_week(user, start):", "slots = ['breakfast', 'lunch', 'dinner']",
           "# TODO: skip days marked as eating out"],
          "Writing the function that lays out a week of meals."),
        w("Safari", "Drag and drop a week calendar with Alpine.js - CSS Recipes", url("https://cssrecipes.example/week-drag"),
          ["x-on:drop moves a meal from one day to another", "Keep the day cells as drop targets"],
          "Reading how to drag meals between the days of a week grid."),
        w("GitHub", "Add weekly meal planner by Dev - Pull Request #81 - pantry-labs/recipe-box",
          url("https://github.com/pantry-labs/recipe-box/pull/81"),
          ["Draft", "4 files changed", "app/planner.py, templates/planner.html"],
          "Opening a draft pull request for the meal planner."),
        w("Slack", "#recipe-box-dev", {},
          ["Dev: can the planner show leftovers from the night before?", "Isha: yes, add a leftovers slot"],
          "Chatting with the team about what the meal planner should show."),
        w("Figma", "Planner screen - Recipe Box UI", {},
          ["Week view, 7 columns", "Meal chip: photo, name, minutes"],
          "Looking at the design of the week view."),
    ], [inbox(23, "Your grocery delivery is on its way", "Pantry Labs standup notes", "Invoice for hosting")],
        twin="rb-image-upload")
    yield story("rb-image-upload", "fixing photo uploads that fail in a recipe app", [
        w("Terminal", "recipe-box (fix/image-upload) - zsh", cwd(rb),
          ["$ git checkout -b fix/image-upload", "$ curl -F photo=@big.jpg localhost:5000/recipes/12/photo",
           "413 Request Entity Too Large"],
          "Reproducing a failing photo upload from the terminal."),
        w("Visual Studio Code", "uploads.py - recipe-box", file(f"{rb}/app/uploads.py"),
          ["MAX_BYTES = 5 * 1024 * 1024", "if len(data) > MAX_BYTES: abort(413)",
           "img = Image.open(buf); img.thumbnail((1600, 1600))"],
          "Changing the upload code to shrink big photos instead of rejecting them."),
        w("Safari", "ImageOps.exif_transpose - Pillow documentation", url("https://pillow.example/docs/ImageOps"),
          ["Transpose a PIL.Image by the EXIF Orientation tag"],
          "Reading the Pillow docs about rotating photos that come from phones."),
        w("GitHub", "Photos over 5 MB fail to save - Issue #77 - pantry-labs/recipe-box",
          url("https://github.com/pantry-labs/recipe-box/issues/77"),
          ["Reported by 3 users", "Works on Wi-Fi, fails on phone camera photos"],
          "Reading the bug report about photos that will not save."),
        w("Slack", "#recipe-box-dev", {},
          ["Isha: a customer sent a 9 MB photo of her lasagna", "Dev: I'll resize on the server"],
          "Chatting with the team about a customer's photo that would not upload."),
        w("Safari", "client_max_body_size - nginx documentation", url("https://nginx.example/docs/client_max_body_size"),
          ["Sets the maximum allowed size of the client request body"],
          "Checking the web server's size limit for uploads."),
    ], [inbox(34, "Pantry Labs standup notes", "Your Wi-Fi bill is ready", "New comment on issue #77")],
        twin="rb-meal-planner")

    ps = "~/code/pottery-site"
    yield story("ps-blog-move", "moving a pottery shop's blog to a static site", [
        w("Safari", "Tools - Export - Kiln & Clay Blog - WordPress", url("https://kilnandclay.example/wp-admin/export.php"),
          ["Choose what to export", "All content", "Download Export File"],
          "Exporting the old blog's posts from WordPress."),
        w("Terminal", "pottery-site - zsh", cwd(ps),
          ["$ npx wp2md export.xml --out content/blog", "Converted 142 posts", "38 images still point at the old site"],
          "Converting exported blog posts into files for the new site."),
        w("Visual Studio Code", "2023-05-glaze-tests.md - pottery-site", file(f"{ps}/content/blog/2023-05-glaze-tests.md"),
          ["title: Glaze tests for the summer",
           "![shino test tiles](https://oldblog.kilnandclay.example/wp-content/uploads/shino.jpg)"],
          "Fixing image links inside a converted blog post."),
        w("Safari", "Redirects - kilnandclay - Netlify", url("https://app.netlify.example/sites/kilnandclay/redirects"),
          ["/blog/:slug  /journal/:slug  301", "Add redirect rule"],
          "Setting up redirects so old blog addresses still work."),
        w("Safari", "Collections - Eleventy documentation", url("https://eleventy.example/docs/collections"),
          ["Create collections from content in folders with tags"],
          "Reading how the site generator builds a list of posts."),
        w("Safari", "Pages - Search Console", url("https://search.example/console/pages"),
          ["Page indexing", "Not found (404): 63 pages"],
          "Checking for broken old blog addresses in the search console."),
    ], [inbox(18, "Your Netlify deploy succeeded", "Clay delivery on Thursday", "Workshop booking: two seats")],
        twin="ps-accessibility")
    yield story("ps-accessibility", "fixing accessibility problems on a pottery shop's website", [
        w("Google Chrome", "Lighthouse - Accessibility 71 - localhost:4000", url("http://localhost:4000/shop/"),
          ["Accessibility score 71", "Background and foreground colors do not have a sufficient contrast ratio",
           "Image elements do not have [alt] attributes"],
          "Reading an accessibility check of the shop pages."),
        w("Visual Studio Code", "styles.css - pottery-site", file(f"{ps}/src/styles.css"),
          [".price { color: #b9a58f; } /* 2.1:1 on white */", ".price { color: #6b5a45; }"],
          "Darkening low-contrast text colours in the stylesheet."),
        w("Visual Studio Code", "product.njk - pottery-site", file(f"{ps}/src/product.njk"),
          ['<img src="{{ item.photo }}" alt="">', '<img src="{{ item.photo }}" alt="{{ item.photoAlt }}">'],
          "Adding alt text to product photo tags."),
        w("Terminal", "pottery-site - zsh", cwd(ps),
          ["$ npx pa11y-ci", "http://localhost:4000/shop/ - 9 errors", "http://localhost:4000/ - 2 errors"],
          "Running an automated accessibility checker on the pages."),
        w("Safari", "Understanding Success Criterion 1.4.3: Contrast (Minimum) - W3C",
          url("https://w3.example/WAI/WCAG/Understanding/contrast-minimum"),
          ["Text has a contrast ratio of at least 4.5:1"],
          "Reading the contrast rule the shop pages need to meet."),
        w("Gmail", "Re: Can't read the shop on my phone - Kiln & Clay", {},
          ["The grey prices are very hard to read for me", "Thanks for letting us know, fixing this week"],
          "Replying to a customer who could not read the prices."),
    ], [inbox(27, "Your Netlify deploy succeeded", "Clay delivery on Thursday", "Workshop booking: two seats")],
        twin="ps-blog-move")

    yield story("school-fair", "organising the school fair", [
        w("Microsoft Excel", "Timeline_v1.xlsx", file("~/Documents/SchoolFair/Timeline_v1.xlsx"),
          ["Fri 16:00 stalls delivered", "Sat 08:30 volunteers arrive", "Sat 10:00 gates open"],
          "Drafting the school fair's day-by-day timeline."),
        w("WhatsApp", "Fair volunteers (Class 5)", {},
          ["Anita: I can do the cake stall 10 to 12", "We need two more for the games table"],
          "Sorting out volunteers for fair stalls in a parents' group."),
        w("Gmail", "Re: Permission for the field on 18 Oct - Greenfield Primary", {},
          ["The field is yours from 3 pm Friday", "Please return the key by 6 pm Saturday"],
          "Reading the school office's reply about using the field."),
        w("Microsoft Excel", "Timeline_v2.xlsx", file("~/Documents/SchoolFair/Timeline_v2.xlsx"),
          ["Sat 09:00 stalls open (moved from 10:00)", "Sat 15:00 prize draw"],
          "Revising the fair timeline after the school's reply."),
        w("Canva", "Fair poster A3 - Canva", {},
          ["Greenfield Primary School Fair", "Saturday 18 October, 10 am"],
          "Designing the poster for the school fair."),
        w("Microsoft Excel", "Timeline_final.xlsx", file("~/Documents/SchoolFair/Timeline_final.xlsx"),
          ["Sat 09:00 stalls open", "Sat 15:00 prize draw", "Sat 17:30 clear-up"],
          "Finalising the fair timeline."),
    ], [inbox(40, "Class 5 parents: fair update", "Your parcel was delivered", "Library books due Friday")],
        twin="loft-conversion")
    yield story("loft-conversion", "converting the loft into a bedroom", [
        w("Microsoft Excel", "Timeline_v1.xlsx", file("~/Documents/LoftBuild/Timeline_v1.xlsx"),
          ["Wk 1 scaffold up", "Wk 2 roof window cut-in", "Wk 4 plumber first fix"],
          "Drafting the loft conversion's week-by-week timeline."),
        w("Gmail", "Re: Quote for dormer and roof windows - Hartley Builders", {},
          ["Dormer frame has a 3 week lead time", "Windows are delivered in week 2 if ordered by Friday"],
          "Reading the builder's reply about lead times."),
        w("Safari", "Planning permission for loft conversions - Planning Guide",
          url("https://planningguide.example/loft-conversions"),
          ["Most loft conversions are permitted development", "Volume limit of 40 cubic metres for terraced houses"],
          "Checking whether the loft work needs planning permission."),
        w("Microsoft Excel", "Timeline_v2.xlsx", file("~/Documents/LoftBuild/Timeline_v2.xlsx"),
          ["Wk 3 roof window cut-in (moved: lead time)", "Wk 6 plaster"],
          "Revising the build timeline after the builder's lead times."),
        w("Preview", "Structural_calc_loft.pdf", file("~/Documents/LoftBuild/Structural_calc_loft.pdf"),
          ["Steel beam 203x133x25", "Bearing on the existing wall plate"],
          "Looking over the engineer's structural calculation."),
        w("Microsoft Excel", "Timeline_final.xlsx", file("~/Documents/LoftBuild/Timeline_final.xlsx"),
          ["Wk 3 roof window cut-in", "Wk 6 plaster", "Wk 9 decorating"],
          "Finalising the loft build timeline."),
    ], [inbox(36, "Hartley Builders: site visit Tuesday", "Your parcel was delivered", "Council tax statement")],
        twin="school-fair")

    yield story("orchard-brand-guide", "writing a brand guide for a dental clinic", [
        w("Microsoft Word", "Brand_Guide_v1.docx", file("~/Clients/OrchardDental/Brand_Guide_v1.docx"),
          ["Voice: warm, reassuring, never clinical", "Words we avoid: drill, pain, needle"],
          "Writing the voice section of a dental clinic's brand guide."),
        w("Gmail", "Re: Tone of voice questions - Orchard Dental", {},
          ["Dr. Kapoor: please keep it gentle, many of our patients are nervous children",
           "We would like the guide by the 20th"],
          "Reading a dentist's answers about how the clinic should sound."),
        w("Safari", "Plain language for dental practices - Practice Journal", url("https://practicejournal.example/plain-language"),
          ["Say 'a little pinch' rather than 'injection'", "Explain each step before you do it"],
          "Researching gentle wording for talking to nervous patients."),
        w("Figma", "Orchard Dental - Logo and colors", {},
          ["Teal 1FA3A0", "Rounded sans-serif"],
          "Looking at the clinic's logo and colours to match the guide."),
        w("Microsoft Word", "Brand_Guide_v2.docx", file("~/Clients/OrchardDental/Brand_Guide_v2.docx"),
          ["Voice: warm, reassuring, playful with children", "Sample line: 'Your smile is in good hands.'"],
          "Revising the dental clinic's brand guide after feedback."),
        w("Zoom", "Zoom Meeting - Orchard Dental review", {},
          ["Dr. Kapoor, Reception lead", "Screen share: Brand_Guide_v2"],
          "In a video call walking the clinic through the guide."),
    ], [inbox(52, "Invoice 114 sent to Orchard Dental", "Your coworking desk renewal", "Newsletter: The Copy Desk")],
        twin="peak-brand-guide")
    yield story("peak-brand-guide", "writing a brand guide for an outdoor gear shop", [
        w("Microsoft Word", "Brand_Guide_v1.docx", file("~/Clients/PeakOutfitters/Brand_Guide_v1.docx"),
          ["Voice: bold, practical, a bit cheeky", "Words we avoid: luxury, extreme"],
          "Writing the voice section of an outdoor gear shop's brand guide."),
        w("Gmail", "Re: Tone of voice questions - Peak Outfitters", {},
          ["Rohan: we sound like a friend who has done the climb, not a catalogue",
           "Can we have the guide by the 22nd?"],
          "Reading the shop owner's answers about how the brand should sound."),
        w("Safari", "Copy that sells gear - The Copy Desk", url("https://copydesk.example/gear-copy"),
          ["Lead with the trail, not the specs", "One honest sentence beats three adjectives"],
          "Researching how outdoor brands write about gear."),
        w("Figma", "Peak Outfitters - Logo and colors", {},
          ["Rust C8501E", "Condensed bold type"],
          "Looking at the shop's logo and colours to match the guide."),
        w("Microsoft Word", "Brand_Guide_v2.docx", file("~/Clients/PeakOutfitters/Brand_Guide_v2.docx"),
          ["Voice: bold, practical, a bit cheeky", "Sample line: 'Built for the second hour of the climb.'"],
          "Revising the outdoor shop's brand guide after feedback."),
        w("Zoom", "Zoom Meeting - Peak Outfitters review", {},
          ["Rohan, Store manager", "Screen share: Brand_Guide_v2"],
          "In a video call walking the shop through the guide."),
    ], [inbox(47, "Invoice 115 sent to Peak Outfitters", "Your coworking desk renewal", "Newsletter: The Copy Desk")],
        twin="orchard-brand-guide")

    yield story("family-health-cover", "choosing a health insurance plan for a family of four", [
        w("Safari", "Compare family health plans - CoverCompare", url("https://covercompare.example/health/family"),
          ["Family of 4, sum insured 10,00,000", "Premium 24,500 per year", "Waiting period for existing conditions: 3 years"],
          "Comparing health insurance plans for a family of four."),
        w("Microsoft Excel", "Plans comparison.xlsx", file("~/Documents/Family/Plans comparison.xlsx"),
          ["Plan, Premium, Room rent cap, Existing-condition wait", "Plan A, 24500, none, 3 yrs", "Plan B, 27800, none, 2 yrs"],
          "Putting health plans side by side in a spreadsheet."),
        w("WhatsApp", "Sneha", {},
          ["Papa's BP medicines are covered only after 3 years?", "Then plan B is better, it is 2 years"],
          "Discussing with a spouse which plan covers a parent's existing condition."),
        w("Gmail", "Re: Quote for 4 members - Mehta Insurance Advisors", {},
          ["Attached: quotes for you, Sneha and the two kids", "Please send last year's medical reports"],
          "Reading an insurance advisor's quote for the whole family."),
        w("Safari", "Cashless hospitals near Pune - CoverCompare network", url("https://covercompare.example/network/pune"),
          ["Network hospitals in Pune: 118", "Ruby Hall Clinic, Jehangir Hospital"],
          "Checking which nearby hospitals accept the plans."),
        w("Preview", "Policy_wording_PlanB.pdf", file("~/Documents/Family/Policy_wording_PlanB.pdf"),
          ["Exclusions: cosmetic treatment", "Maternity cover after 2 years"],
          "Reading the fine print of a health plan."),
    ], [inbox(33, "Premium reminder: car policy", "Mehta Insurance Advisors", "School fee receipt")],
        twin="japan-trip-cover")
    yield story("japan-trip-cover", "buying travel insurance for a month in Japan", [
        w("Safari", "Compare travel insurance - CoverCompare", url("https://covercompare.example/travel"),
          ["Trip to Japan, 30 days, 2 travellers", "Premium 3,900", "Baggage loss up to 50,000"],
          "Comparing travel insurance for a month in Japan."),
        w("Microsoft Excel", "Trip cover options.xlsx", file("~/Documents/Japan/Trip cover options.xlsx"),
          ["Plan, Premium, Medical limit, Baggage, Trip delay", "Plan X, 3900, 50 lakh, 50000, 6 hrs"],
          "Putting travel insurance options side by side in a spreadsheet."),
        w("WhatsApp", "Kabir", {},
          ["Does it cover the ski day in Nagano?", "Adventure sports add-on is +900"],
          "Discussing with a travel partner whether skiing is covered."),
        w("Gmail", "Re: Flight change - departure 14 Mar", {},
          ["Your new departure is 14 March 07:10", "Cover must start on the day you leave"],
          "Reading an airline email that changes the trip dates."),
        w("Safari", "Hospital costs for visitors in Japan - Travel Health Guide", url("https://travelhealth.example/japan"),
          ["Visitors are not in the national scheme", "A clinic visit can cost 15,000 yen"],
          "Reading what a doctor's visit costs a tourist in Japan."),
        w("Preview", "Travel_policy_wording.pdf", file("~/Documents/Japan/Travel_policy_wording.pdf"),
          ["Exclusions: adventure sports unless added", "Claims within 30 days of return"],
          "Reading the fine print of a travel policy."),
    ], [inbox(29, "Your visa slot is confirmed", "Bank statement for October", "Yen exchange rates")],
        twin="family-health-cover")

    yield story("hills-offsite", "planning a team offsite in the hills", [
        w("Slack", "Meera Rao (DM)", {},
          ["Can you check if Lonavala has space for 22 on the 14th?", "Budget cap is 1,500 per head"],
          "Chatting with a colleague about where to hold the team offsite."),
        w("Safari", "Hill View Resort - Conference rooms and packages", url("https://hillview.example/conference"),
          ["Capacity 30, projector, 2 breakout rooms", "Day package from 1,350 per head"],
          "Looking at a resort's offsite packages."),
        w("Google Sheets", "Offsite headcount and dietary", {},
          ["Name, Dietary, Room share", "Arjun, vegetarian, with Dev"],
          "Tracking who is coming to the offsite and what they eat."),
        w("Calendar", "Calendar - November", {},
          ["14 Nov: Offsite (all day)", "Block the team's calendars"],
          "Blocking the offsite day in the team calendar."),
        w("Gmail", "Quote: Hill View Resort offsite, 22 guests", {},
          ["Quote valid until 5 Nov", "50% advance to confirm"],
          "Reading a resort's quote for the offsite."),
        w("Google Slides", "Offsite agenda - draft", {},
          ["09:30 Welcome", "11:00 Planning for the next half", "16:00 Games"],
          "Drafting the offsite's agenda."),
    ], [inbox(61, "Standup notes", "Expense report approved", "Lunch order for Thursday")],
        twin="quarter-numbers")
    yield story("quarter-numbers", "preparing the quarterly numbers for a review", [
        w("Slack", "Meera Rao (DM)", {},
          ["Can you pull Q3 churn by plan for the review?", "I need it by Thursday"],
          "Chatting with a colleague about numbers for the quarterly review."),
        w("Google Sheets", "Q3 metrics pack - raw", {},
          ["Month, New, Churned, Net", "Jul, 412, 96, 316"],
          "Checking monthly customer numbers in a sheet."),
        w("Safari", "Churn by plan - Metabase", url("https://metrics.example/dashboard/churn-by-plan"),
          ["Starter 4.1%, Pro 2.2%, Team 1.4%"],
          "Looking at a dashboard of churn by plan."),
        w("Calendar", "Calendar - November", {},
          ["13 Nov: Quarterly review 3:00 pm", "Meera, Arjun, Dev"],
          "Checking when the quarterly review is."),
        w("Google Slides", "Q3 review - draft", {},
          ["Net adds 1,030 (+8%)", "Churn down from 3.1% to 2.7%"],
          "Drafting slides for the quarterly review."),
        w("Gmail", "Re: Q3 numbers - mismatch on the Pro plan", {},
          ["Billing shows 1,418 Pro accounts, the dashboard shows 1,402", "Can you reconcile before the review?"],
          "Reading an email about a small mismatch in the numbers."),
    ], [inbox(63, "Standup notes", "Expense report approved", "Dashboard access request")],
        twin="hills-offsite")

    yield story("dinner-party", "cooking a dinner for eight on Saturday", [
        w("Notes", "Saturday dinner - menu", {},
          ["Starter: burrata and roasted tomatoes", "Main: lamb shoulder, 4 hours", "Vegetarian: stuffed aubergine"],
          "Planning the menu for a Saturday dinner for eight."),
        w("Safari", "Slow-roast lamb shoulder with rosemary - Kitchen Notes", url("https://kitchennotes.example/slow-roast-lamb"),
          ["Serves 8, 4 hours", "Rest for 30 minutes before carving"],
          "Reading a recipe for the main dish."),
        w("WhatsApp", "Dinner Sat", {},
          ["Priya: I'm bringing dessert", "Karan doesn't drink, any mocktail?"],
          "Sorting out who brings what for a dinner with friends."),
        w("Safari", "Wines to pair with lamb - Vine and Table", url("https://vineandtable.example/lamb"),
          ["Syrah or Rioja Reserva", "Allow one bottle per two guests"],
          "Choosing wine to go with the main dish."),
        w("Notes", "Shopping list - Saturday", {},
          ["lamb shoulder 2.2 kg", "aubergines x4", "burrata x3"],
          "Writing a shopping list for the dinner."),
        w("Safari", "Setting a table for 8 - Home and Hosting", url("https://homeandhosting.example/table-for-8"),
          ["Candles, place cards, a long runner"],
          "Looking at ideas for setting the table for guests."),
    ], [inbox(19, "Your grocery delivery slot", "Priya sent you a message", "Electricity bill")],
        twin="weekly-meal-prep")
    yield story("weekly-meal-prep", "cooking food ahead for the work week", [
        w("Notes", "Meal prep - week of 10 Nov", {},
          ["Mon to Thu lunches: chicken, rice, broccoli", "Snacks: boiled eggs, hummus"],
          "Planning lunches and snacks to cook ahead for the week."),
        w("Safari", "Batch-cook lentil soup - Kitchen Notes", url("https://kitchennotes.example/batch-lentil-soup"),
          ["Makes 6 portions", "Freezes for 3 months"],
          "Reading a recipe that can be cooked in bulk."),
        w("Safari", "Glass or plastic meal prep containers - Home and Hosting", url("https://homeandhosting.example/containers"),
          ["Glass keeps smells out; plastic is lighter"],
          "Comparing food containers for packed lunches."),
        w("Google Sheets", "Portions tracker", {},
          ["Meal, Servings, Cost per serving", "Chicken rice bowl, 4, 62"],
          "Tracking servings and cost of prepared meals."),
        w("Notes", "Shopping list - meal prep", {},
          ["chicken thighs 1.5 kg", "brown rice 1 kg", "broccoli x4"],
          "Writing a shopping list for the week's cooking."),
        w("YouTube", "Sunday meal prep in 90 minutes", url("https://video.example/watch?v=mp90"),
          ["Cook the rice and roast the vegetables at the same time"],
          "Watching a video about cooking a week of meals in one go."),
    ], [inbox(22, "Your grocery delivery slot", "Cooking class timetable", "Electricity bill")],
        twin="dinner-party")

    yield story("laptop-for-editing", "choosing a laptop for video editing work", [
        w("Safari", "Best laptops for video editing - LaptopLab", url("https://laptoplab.example/best-for-editing"),
          ["16 GB of RAM is the minimum, 32 GB for 4K timelines", "Look for a screen with full sRGB coverage"],
          "Reading a review of laptops for video editing."),
        w("Safari", "ProBook 16 Studio review - LaptopLab", url("https://laptoplab.example/probook-16-studio"),
          ["Export of a 10 minute 4K clip: 3 min 40 s", "Fans are loud under load"],
          "Reading a review of one editing laptop."),
        w("Microsoft Excel", "Laptop shortlist.xlsx", file("~/Documents/Laptops/Laptop shortlist.xlsx"),
          ["Model, RAM, Graphics, Price", "ProBook 16 Studio, 32 GB, RTX, 1,89,000"],
          "Comparing shortlisted editing laptops by memory and graphics."),
        w("YouTube", "Is 16GB enough for video editing in 2026?", url("https://video.example/watch?v=16gb"),
          ["The timeline stutters with several 4K layers"],
          "Watching a video about memory needs for video editing."),
        w("Safari", "System requirements - editing software help", url("https://editsoft.example/help/system-requirements"),
          ["Recommended: 32 GB of RAM, 8 GB of graphics memory"],
          "Checking the editing software's recommended specs."),
        w("WhatsApp", "Rahul (client)", {},
          ["Can you deliver 4K this time?", "Your old laptop kept crashing last time"],
          "Chatting with a client who needs 4K video work."),
    ], [inbox(44, "Order shipped", "Client feedback on the reel", "Cloud storage almost full")],
        twin="laptop-for-mother")
    yield story("laptop-for-mother", "choosing a laptop for my mother", [
        w("Safari", "Best laptops for seniors - LaptopLab", url("https://laptoplab.example/best-for-seniors"),
          ["Look for a bright screen and large keys", "Keep it simple: 8 GB of RAM is plenty"],
          "Reading a review of laptops that suit older users."),
        w("Safari", "SlimBook 14 Easy review - LaptopLab", url("https://laptoplab.example/slimbook-14-easy"),
          ["The touchpad is large and forgiving", "Battery lasts 12 hours"],
          "Reading a review of one easy-to-use laptop."),
        w("Microsoft Excel", "Laptop shortlist.xlsx", file("~/Documents/Mummy/Laptop shortlist.xlsx"),
          ["Model, Screen, Weight, Price", "SlimBook 14 Easy, 14 in, 1.2 kg, 52,000"],
          "Comparing shortlisted laptops by screen size, weight and price."),
        w("YouTube", "Bigger text and cursor on a laptop, step by step", url("https://video.example/watch?v=bigtext"),
          ["Settings, Accessibility, Text size"],
          "Watching a video about making text bigger on a laptop."),
        w("WhatsApp", "Didi", {},
          ["Mummy wants to see everyone on video calls", "Under 55k please, she won't use anything fancy"],
          "Chatting with a sibling about what a mother needs from a laptop."),
        w("Safari", "Video calls on a laptop for beginners - Tech for Seniors", url("https://techforseniors.example/video-calls"),
          ["Click the green button to join"],
          "Reading a beginner's guide to video calls."),
    ], [inbox(37, "Order shipped", "Didi: Mummy's birthday plan", "Cloud storage almost full")],
        twin="laptop-for-editing")


def pivots():
    """Six projects that change topic half-way, with a bridge window carrying both topics."""
    e, b, l = "early", "bridge", "late"
    yield story("zine-to-shop", "a hand-printed zine that grows into a small online shop", [
        w("Affinity Publisher", "Moth Notes issue 1.afpub", file("~/Zine/Moth Notes issue 1.afpub"),
          ["Page 5: field notes on hawk moths", "A5 booklet, 16 pages"],
          "Laying out the pages of a small zine about moths.", e),
        w("Safari", "File prep and colour layers - Print Club", url("https://printclub.example/riso-file-prep"),
          ["Separate each ink colour into its own greyscale layer"],
          "Reading how to prepare artwork for risograph printing.", e),
        w("Gmail", "Quote: 100 copies A5, 2 colours - Little Press Studio", {},
          ["100 copies, 16 pages, 2 colours: 3,200", "Turnaround 5 working days"],
          "Reading a print studio's quote for a run of zines.", e),
        w("Notes", "Moth Notes - what's next", {},
          ["Issue 1 sold out at the market stall in a day",
           "Idea: sell issue 1 and prints online, a small shop instead of one-off stalls",
           "Need: a shop, shipping, product photos"],
          "Writing down the plan to turn a zine into a small online shop.", b),
        w("Safari", "Add product - Inkshop admin", url("https://inkshop.example/admin/products/new"),
          ["Product: Moth Notes issue 1 (zine)", "Weight 60 g", "Stock 40"],
          "Adding the zine as a product in an online shop's admin.", l),
        w("Safari", "Small packet rates - India Post", url("https://post.example/small-packet-rates"),
          ["Up to 100 g: 45 within India"],
          "Checking postage rates for sending small parcels.", l),
        w("Safari", "Product photos at home with a window and white card - Shop Tips", url("https://shoptips.example/home-photos"),
          ["Shoot near a window and bounce the light with white card"],
          "Reading how to photograph products at home.", l),
    ], [inbox(26, "Market stall slot confirmed", "Your parcel was delivered", "Paper order dispatched")], pivot=True)
    yield story("soil-to-satellite", "a thesis chapter that moves from field sensors to satellite images", [
        w("Arduino IDE", "soil_logger | Arduino IDE 2.3", file("~/thesis/soil_logger/soil_logger.ino"),
          ["int raw = analogRead(A0);", "float vwc = map(raw, 520, 260, 0, 100);"],
          "Writing code that reads a soil moisture sensor.", e),
        w("Microsoft Excel", "plot_readings_march.xlsx", file("~/thesis/data/plot_readings_march.xlsx"),
          ["Plot, Depth cm, Moisture %", "A3, 15, 24.1"],
          "Looking over soil moisture readings from field plots.", e),
        w("Safari", "Capacitive soil moisture sensor v1.2 datasheet", url("https://sensors.example/soil-v1-2.pdf"),
          ["Operating voltage 3.3 to 5.5 V", "Output 1.2 to 2.5 V"],
          "Reading the datasheet of a soil moisture sensor.", e),
        w("Gmail", "Re: Chapter 3 direction - Prof. Iyer", {},
          ["Sensors only cover 12 plots; satellite estimates cover the whole district",
           "Keep the sensor readings as ground truth to check the satellite numbers"],
          "Reading the supervisor's advice to use satellite estimates checked against sensor readings.", b),
        w("Safari", "Level-2A scene search - Copernicus Browser", url("https://copernicus.example/browser"),
          ["Tile 43QDV, cloud cover 4%", "Download bands B04, B08, B11"],
          "Choosing satellite images of the study area.", l),
        w("JupyterLab", "ndvi_maps.ipynb - JupyterLab", file("~/thesis/notebooks/ndvi_maps.ipynb"),
          ["import rasterio", "ndvi = (nir - red) / (nir + red)"],
          "Computing a vegetation index from satellite bands.", l),
        w("Safari", "Soil moisture from radar backscatter - Remote Sensing Reviews", url("https://rsreviews.example/radar-soil-moisture"),
          ["Backscatter at VV polarisation tracks surface moisture"],
          "Reading a paper on estimating soil moisture from radar images.", l),
    ], [inbox(35, "Prof. Iyer: meeting moved", "Library fine notice", "Conference deadline reminder")], pivot=True)
    yield story("book-swap-to-tool-library", "a street book swap that becomes a lending corner for tools too", [
        w("Canva", "Lane 4 Book Swap flyer - Canva", {},
          ["Take a book, leave a book", "Little shelf outside No. 12"],
          "Designing a flyer for a neighbourhood book swap shelf.", e),
        w("Safari", "Build a weatherproof shelf - Woodworks Blog", url("https://woodworks.example/weatherproof-shelf"),
          ["Use 18 mm exterior plywood", "Slope the roof 15 degrees so rain runs off"],
          "Reading how to build a weatherproof shelf for the book swap.", e),
        w("WhatsApp", "Lane 4 readers", {},
          ["Meena: first books are in!", "Someone left a pile of crime novels"],
          "Chatting in the neighbours' group about the new book shelf.", e),
        w("Google Docs", "Lending corner plan - books and tools", {},
          ["Phase 1: books (done)", "Phase 2: tools, with a sign-out sheet, a deposit and care rules",
           "Sunil: people keep asking to borrow a drill"],
          "Writing a plan to lend tools as well as books.", b),
        w("Google Sheets", "Tool inventory", {},
          ["Item, Condition, Owner, Deposit", "Cordless drill, good, Sunil, 500"],
          "Listing tools that neighbours offer to lend.", l),
        w("Safari", "Lending tools to neighbours: who is responsible - Community Law Guide", url("https://communitylaw.example/lending-tools"),
          ["A signed waiver does not remove all risk", "Check household insurance"],
          "Reading about who is responsible if a lent tool causes harm.", l),
        w("Microsoft Word", "Tool lending rules.docx", file("~/Documents/Lane4/Tool lending rules.docx"),
          ["Return within 3 days", "Clean the tool before returning it"],
          "Writing the rules for borrowing tools.", l),
    ], [inbox(14, "Lane 4 residents newsletter", "Your parcel was delivered", "Water bill")], pivot=True)
    yield story("cardgame-to-app", "a card game design that becomes a phone app", [
        w("Figma", "Hedgerow - card templates", {},
          ["Creature card 63 x 88 mm", "Cost, Power, Ability text"],
          "Designing the card layouts for a card game.", e),
        w("Google Docs", "Hedgerow rulebook v3", {},
          ["Each turn: draw, play one creature, attack", "A player with no seeds left loses"],
          "Editing the rules of a card game.", e),
        w("Notes", "Playtest notes - Saturday", {},
          ["Too slow when two players hold 5 or more cards", "The Burrow ability is too strong"],
          "Writing down what went wrong in a playtest of the card game.", e),
        w("Google Docs", "Hedgerow digital - scope", {},
          ["Same rules as rulebook v3, played on phones", "Turn timer; pass-and-play first, online later",
           "No new cards in the first version"],
          "Scoping a phone version of the card game.", b),
        w("Safari", "Build a card game - Flame engine, Flutter docs", url("https://flutterdocs.example/flame/card-game"),
          ["class CardComponent extends PositionComponent"],
          "Following a tutorial for building a card game in Flutter.", l),
        w("Visual Studio Code", "card_component.dart - hedgerow_app", file("~/code/hedgerow_app/lib/card_component.dart"),
          ["class CardComponent extends PositionComponent with TapCallbacks", "void onTapDown(TapDownEvent event)"],
          "Writing the code for a card that can be tapped on a phone.", l),
        w("Safari", "Hedgerow - TestFlight - App Store Connect", url("https://appstore.example/testflight/hedgerow"),
          ["Build 0.1 (1): Processing", "Add internal testers"],
          "Uploading a first test build for friends to try on their phones.", l),
    ], [inbox(20, "Print shop: card stock samples", "Your parcel was delivered", "Game night on Friday")], pivot=True)
    yield story("sheet-to-tutor-app", "a tutor's fee spreadsheet that becomes a small web app", [
        w("Microsoft Excel", "Students_tracker.xlsx", file("~/Tutoring/Students_tracker.xlsx"),
          ["Student, Subject, Day, Fee paid", "Aarav, Maths, Tue, Yes"],
          "Updating the sheet that tracks students and fees.", e),
        w("Microsoft Excel", "Fees_due_pivot.xlsx", file("~/Tutoring/Fees_due_pivot.xlsx"),
          ["Row labels, Sum of Due", "Oct, 18,500"],
          "Summarising fees due per month with a pivot table.", e),
        w("Safari", "XLOOKUP and VLOOKUP compared - Sheet Tips", url("https://sheettips.example/xlookup"),
          ["XLOOKUP can look left and returns exact matches by default"],
          "Reading how to look up a student's row in a sheet.", e),
        w("Notes", "Tutoring - the sheet is breaking", {},
          ["Two people edited at once and overwrote each other",
           "Move to a small web app: students, sessions, fees", "Keep the sheet as the starting data"],
          "Noting that the sheet should become a small web app.", b),
        w("Visual Studio Code", "schema.sql - tutor-app", file("~/code/tutor-app/schema.sql"),
          ["CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, subject TEXT);",
           "CREATE TABLE payments (student_id INTEGER, month TEXT, amount INTEGER);"],
          "Writing database tables for students and payments.", l),
        w("Terminal", "tutor-app - zsh", cwd("~/code/tutor-app"),
          ["$ flask run", "* Running on http://127.0.0.1:5000", "$ python import_sheet.py Students_tracker.xlsx",
           "Imported 64 students"],
          "Starting the web app and importing the old sheet's rows.", l),
        w("Safari", "Sessions and cookies - Flask documentation", url("https://flask.example/docs/sessions"),
          ["session['user_id'] = user.id"],
          "Reading how to keep a tutor signed in on the web app.", l),
    ], [inbox(30, "Parent: fee receipt please", "Your parcel was delivered", "Exam timetable")], pivot=True)
    yield story("poster-to-eye-camp", "a clinic poster that turns into organising a free eye check-up camp", [
        w("Canva", "Poster - Eye care awareness (A3)", {},
          ["Get your eyes checked", "Sunrise Community Clinic"],
          "Designing a poster about eye care for a community clinic.", e),
        w("Safari", "Legible fonts for older readers - Design for Good", url("https://designforgood.example/legible-fonts"),
          ["Use at least 28 pt for body text on posters"],
          "Reading how to make a poster easy to read for older people.", e),
        w("Gmail", "Re: Poster text approval - Dr. Menon", {},
          ["Please add the clinic's phone number and timings", "Remove the word 'cure'"],
          "Reading the doctor's changes to the poster text.", e),
        w("Google Docs", "Poster meeting notes", {},
          ["The poster is for a free eye check-up camp on 23 Nov",
           "Need: 2 optometrists, 150 slips, volunteers for the queue, a hall"],
          "Reading meeting notes saying the poster is for a free eye camp.", b),
        w("Google Sheets", "Camp volunteers and roster", {},
          ["Name, Role, Shift", "Kavya, Registration desk, 9 to 12"],
          "Making a roster of volunteers for the day.", l),
        w("Gmail", "Re: Request for the community hall on 23 Nov - Ward 12 Office", {},
          ["The hall is free from 8 am to 2 pm", "Fee waived for registered clinics"],
          "Reading the ward office's reply about booking a hall.", l),
        w("Safari", "Equipment and consent forms - Vision Outreach Guide", url("https://visionoutreach.example/camp-checklist"),
          ["Letter charts, trial lens kit, torches", "Consent form for dilating drops"],
          "Reading a checklist of equipment for an eye camp.", l),
    ], [inbox(24, "Sunrise Clinic: staff rota", "Your parcel was delivered", "Printing quote")], pivot=True)


def build():
    stories = list(twins()) + list(pivots())
    ids = [s["id"] for s in stories]
    assert len(ids) == len(set(ids)), "two stories share an id"
    for s in stories:
        assert len(s["windows"]) >= 6, s["id"]
        for t in s.get("twins", []):
            assert t in ids and s["id"] in next(x for x in stories if x["id"] == t)["twins"], (s["id"], t)
        if s.get("pivot"):
            phases = {x.get("phase") for x in s["windows"]}
            assert phases == {"early", "bridge", "late"}, s["id"]
            order = [x["phase"] for x in s["windows"]]
            assert order == sorted(order, key=["early", "bridge", "late"].index), s["id"]
        else:
            assert not any(x.get("phase") for x in s["windows"]), s["id"]
    return stories


def main():
    stories = build()
    OUT.write_text(json.dumps(stories, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(stories)} stories, {sum(len(s['windows']) for s in stories)} windows -> {OUT}")


if __name__ == "__main__":
    main()
