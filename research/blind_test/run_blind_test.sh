#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/../.."

echo '=== issue #1 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 1 --title 'app crashes when parsing yt video link' --body 'opened everforge put youtube link in the input box and clicked process. screen went completely white. tried restarting app same thing happened again. literally unusable right now please fix asap i need to save this workflow video' | tee research/blind_test/results/issue_01.json

echo '=== issue #2 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 2 --title '"Extract Workflows" button not responding' --body 'Hi team,

I pasted a reddit thread url into EverForge and hit parse. The summary loaded fine, but when I click the "Extract Workflows" button at the bottom right, nothing happens at all. There'\''s no loading spinner or error popup.

I tried refreshing the browser tab, clearing my cache, and trying a different Reddit post, but clicking that button still does nothing. Am I doing something wrong or is the button disabled? Thanks!' --candidates research/blind_test/candidates_02.json | tee research/blind_test/results/issue_02.json

echo '=== issue #3 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 3 --title 'Saved Reddit automation tidbits from yesterday completely vanished / library reset?' --body 'Hey guys, yesterday around 4:30 PM EST I forwarded about 8 Reddit posts from r/automation and r/SaaS into EverForge. It extracted a bunch of awesome AI workflow tidbits and 3 potential market openings for a niche CRM tool. I carefully organized them, edited a few titles, and saved them into my "Q3 SaaS Ideas" folder in the library.

This morning I logged back in around 9 AM to review them for a meeting with my cofounder and the entire Q3 folder is empty?? Actually it shows 2 items now but they are old items from last week and the titles are changed back to "Untitled YouTube Parsing #14".

I didn'\''t log out or change accounts. Where did my parsed data go? Is there a backup or version history? I spent almost 3 hours sorting those workflows yesterday. Please tell me they aren'\''t gone forever.' --candidates research/blind_test/candidates_03.json | tee research/blind_test/results/issue_03.json

echo '=== issue #4 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 4 --title 'Is saved library items disappearing a known issue?' --body 'hey is anyone else losing saved items in the library? i processed a few youtube links 2 days ago and tagged all the market opportunities. came back today to check my library and half the items i parsed are gone and some of the saved tags reverted back to default.

not sure if my local database got wiped or if syncing is broken. is this a known bug or is there something wrong on my end? running on chrome win 11.' --candidates research/blind_test/candidates_04.json | tee research/blind_test/results/issue_04.json

echo '=== issue #5 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 5 --title 'Feature request: Export parsed ideas and workflows to CSV / Excel' --body 'Currently I have to manually highlight and copy-paste every AI workflow and business idea into Google Sheets so I can share them with my team and prioritize them. With dozens of videos forwarded every week this is taking way too long.

Can we get an "Export to CSV" or ".xlsx" button in the library view? Ideally it would export columns for Source URL, Extracted Workflows, Market Openings, and Date Added. Thanks!' --candidates research/blind_test/candidates_05.json | tee research/blind_test/results/issue_05.json

echo '=== issue #6 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 6 --title 'Dark mode please!!' --body 'The bright white background is blinding when I'\''m processing YouTube videos late at night. My eyes are burning lol.

Please add a dark theme toggle in settings or make it respect system dark mode settings. Would make reviewing parsed Reddit posts at night way better.' --candidates research/blind_test/candidates_06.json | tee research/blind_test/results/issue_06.json

echo '=== issue #7 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 7 --title 'Auto notifications when new market openings are extracted / email alerts?' --body 'is there any way to get an email or discord notification whenever everforge finishes parsing a video or finds high-confidence business openings?

right now I send a bunch of youtube links from my phone during the day and then i have to keep manually refreshing the app to see if the extraction finished and what ideas came out. you guys should really add automatic alerts/webhooks for this, it would save so much time checking back repeatedly.' --candidates research/blind_test/candidates_07.json | tee research/blind_test/results/issue_07.json

echo '=== issue #8 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 8 --title 'Bug: App won'\''t automatically execute the extracted AI workflows in my Zapier account' --body 'I forwarded a YouTube video about an automated lead gen workflow. EverForge parsed the steps fine in the preview screen, but when I click on the step "Deploy to Zapier", it just highlights the text instead of connecting to my Zapier account and building the zap.

Tried re-connecting my account in settings but there is no integration option for Zapier. Why doesn'\''t the app let me execute/run the workflows directly? It only extracts text steps. Please fix this bug so it auto-creates the zaps.' --candidates research/blind_test/candidates_08.json | tee research/blind_test/results/issue_08.json

echo '=== issue #9 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 9 --title 'Getting Started guide step 3 references missing "API Keys" tab in Settings' --body 'I'\''m following the setup guide in the README / onboarding documentation. Step 3 says:

"Go to Settings -> API Keys and paste your YouTube Data API key to enable video transcription parsing."

However, in version 2.4, there is no "API Keys" tab under Settings. There'\''s only "Account", "Integrations", and "Appearance". I looked under Integrations and YouTube isn'\''t listed there either.

I'\''m completely stuck on setup because I can'\''t find where to input my key. Looks like the docs are outdated compared to the current UI?' --candidates research/blind_test/candidates_09.json | tee research/blind_test/results/issue_09.json

echo '=== issue #10 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 10 --title 'How do I filter saved ideas by source (Reddit vs YouTube)?' --body 'Hi everyone! Quick question - I'\''ve been using EverForge for a couple weeks now and I have about 50 items in my library. I remember reading somewhere that you can filter your saved library to only show ideas that came from Reddit posts versus YouTube videos, but I can'\''t seem to find where that filter option is located.

Is it on the main library screen or inside settings? Sorry if this is obvious, still getting used to the layout!' --candidates research/blind_test/candidates_10.json | tee research/blind_test/results/issue_10.json

echo '=== issue #11 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 11 --title 'Reddit thread comment depth parsing not working as documented?' --body 'The docs state that when forwarding a Reddit URL, EverForge will "parse up to top 3 comment levels for business ideas and pain points".

However, when I forward long Reddit threads (like reddit.com/r/Entrepreneur/comments/1a2b3c4/thread_title), it seems to only grab the main post and top-level comments. None of the nested replies with actual automation details are showing up in my extracted library items.

I don'\''t know if the parser is broken for nested comments, if there'\''s a setting I missed to enable deep comment parsing, or if the docs mean something else by "3 comment levels". Here is a screenshot of my parsed view (attached below) compared to the actual thread.' --candidates research/blind_test/candidates_11.json | tee research/blind_test/results/issue_11.json

echo '=== issue #12 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 12 --title 'QoL: Remember last selected library sorting option across sessions' --body 'Small tweak request: Every time I open EverForge or reload the page, the library sort defaults to "Date Added (Oldest First)". I always have to manually switch it to "Most Recent" or "Sort by Market Opportunity Score".

Could the app remember your last used sort preference in local storage so we don'\''t have to re-select it every time we open the app? Thanks for the awesome tool!' --candidates research/blind_test/candidates_12.json | tee research/blind_test/results/issue_12.json

echo '=== issue #13 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 13 --title 'Blank screen crash when sharing long YouTube playlist link' --body 'Just wanted to post my experience in case it helps. I was trying to forward a 45-minute YouTube video from my mobile browser using the EverForge web app input. As soon as I hit the parse button, the main layout disappears and the page goes totally blank white.

Checked browser console and seeing Uncaught TypeError: Cannot read properties of undefined (reading '\''extract'\''). Happens every time on both Safari mobile and Chrome desktop when starting a new video parse from the home dashboard.' --candidates research/blind_test/candidates_13.json | tee research/blind_test/results/issue_13.json

echo '=== issue #14 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 14 --title 'Reddit parsing taking 2-3 minutes per post now (used to take 5 seconds)' --body 'Is anyone else experiencing major lag when parsing Reddit posts?

A few weeks ago when I forwarded a link it would extract the business ideas and AI workflows in under 10 seconds. Lately, every single Reddit post takes almost 3 minutes with the loading wheel spinning forever before the summary appears. No error pops up, it eventually finishes, but it'\''s gotten super sluggish recently.' --candidates research/blind_test/candidates_14.json | tee research/blind_test/results/issue_14.json

echo '=== issue #15 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 15 --title 'Need to transfer EverForge account ownership to new team lead / billing invoice request' --body 'Hello support team,

Our team lead who set up our EverForge Pro account with his company email (alex@techcompany.com) is leaving the company this Friday. We need to transfer the primary account email and billing information to sara@techcompany.com without losing our saved library of 300+ parsed YouTube workflows.

Also, where can we download the PDF tax invoice for last month'\''s subscription charge? We didn'\''t receive it in email. Please let us know how to update the account owner.' --candidates research/blind_test/candidates_15.json | tee research/blind_test/results/issue_15.json

echo '=== issue #16 ===' 
uv run surface-zero advise --labels data/label_sets/everforge.json --number 16 --title 'UI glitch: "Market Openings" header overlaps badge count on mobile' --body 'Found a small visual glitch on mobile (iPhone 14 Pro, Safari). In the parsed video card view, the text header "Market Openings" overlaps directly on top of the numerical count badge (e.g. "[3]"), making the number hard to read.

Functional extraction works fine, just looks a bit broken visually. Typo also spotted on that card: "Extractd" is missing the "e".' --candidates research/blind_test/candidates_16.json | tee research/blind_test/results/issue_16.json
