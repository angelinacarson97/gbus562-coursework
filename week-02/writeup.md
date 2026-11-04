## What I Built

For this assignment, I created a Prompt Lab using Claude Code and the Anthropic API. The application allows me to enter prompts, communicate with Claude, continue conversations, and export the results into Markdown files. I used the Prompt Lab to complete four different business tasks for Wildframe Coffee Roasters. These included developing subscriber perks, creating coffee bean cards, responding to customer comments, and evaluating possible locations for a pop-up coffee stand. I saved each conversation separately to document the prompting techniques and results.

## Why This Approach

Task 1 - Technique: zero-shot

I chose zero-shot prompting for the subscriber perks because the task required creative ideas rather than following an existing example. I provided information about Wildframe's customers, brand personality, and formatting requirements so Claude could generate six original ideas. This approach worked well because the instructions were specific enough to guide the response without limiting creativity.

Task 2 - Technique: one-shot

I used one-shot prompting for the coffee bean cards because Wildframe already had an established format. Providing the Huila coffee card as one example helped Claude understand the required labels, order, and writing style. This was especially useful because all three new cards needed to follow the same structure while describing different coffees.

Task 3 - Technique: few-shot

I selected few-shot prompting for the customer responses because matching Wildframe's personality was important. I provided four examples of previous customer comments and company replies. These examples helped Claude understand how to respond to different situations while maintaining a friendly, conversational tone.

Task 4 - Technique: chain-of-thought

I chose chain-of-thought prompting for the pop-up location analysis because the task required several calculations and a financial comparison. I instructed Claude to calculate expected customers, revenue, rental expenses, and the remaining revenue after rent for each location. This approach made it easier to evaluate the alternatives and support the final recommendation with numerical evidence.

## Example Run

## System

_(none)_

## You

You are writing coffee bean cards for Wildframe Coffee Roasters, a specialty coffee company with a warm, plainspoken, slightly irreverent brand voice.

Use the following single example from our Brand Kit to understand the exact house format, labels, order, and writing style:

HUILA, COLOMBIA
Roast: Medium
Tastes like: red apple, brown sugar, cocoa
Brew it: Pour-over or drip
Real talk: The coffee that converted our office's tea drinker. Mostly.

Now create a bean card for each of these three coffees:

1. Yirgacheffe, Ethiopia — Light roast. Notes: jasmine, lemon, bergamot. Best as pour-over.
2. Cerrado, Brazil — Medium-dark roast. Notes: peanut, milk chocolate, low acidity. Good for espresso or French press.
3. Chiapas, Mexico — Medium roast. Notes: almond, caramel, orange peel. Good for drip or AeroPress.

Requirements:
- Produce exactly three cards.
- Follow the example's five-line format.
- Keep the same labels in the same order.
- Use each coffee's correct origin, roast, tasting notes, and brewing recommendations.
- Write a short, original "Real talk" sentence for each coffee that matches Wildframe's personality.
- Do not add introductory or concluding text.

## Claude

YIRGACHEFFE, ETHIOPIA
Roast: Light
Tastes like: jasmine, lemon, bergamot
Brew it: Pour-over
Real talk: Smells like a flower shop, tastes like a wake-up call. We don't make the rules.

CERRADO, BRAZIL
Roast: Medium-dark
Tastes like: peanut, milk chocolate, low acidity
Brew it: Espresso or French press
Real talk: Smooth, nutty, and drama-free. The golden retriever of coffee beans.

CHIAPAS, MEXICO
Roast: Medium
Tastes like: almond, caramel, orange peel
Brew it: Drip or AeroPress
Real talk: Cozy enough for Sunday mornings, sharp enough for Monday ones.


## Reflection

After completing these four tasks, I learned that choosing the right prompting technique can make a noticeable difference in the quality of an AI-generated response. Zero-shot prompting worked well for brainstorming because Claude could generate ideas without being restricted by examples. However, I found one-shot and few-shot prompting especially useful when consistency and tone were important. The coffee bean cards followed the required format, while the customer replies sounded more natural because Claude had examples of Wildframe's communication style.

I think the choice of few-shot prompting made the biggest difference because the customer service responses needed to reflect a specific personality rather than just provide information. The technique I am least certain about is chain-of-thought prompting. Although it helped organize the financial analysis, the initial response was cut off by the token limit, requiring an additional prompt. If I repeated the task, I would request a more concise calculation table and explanation from the beginning. Overall, this assignment showed me that effective prompting involves selecting an approach based on the task, clearly defining expectations, and reviewing the results instead of automatically accepting the first response.
