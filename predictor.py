from api_client import fetch_team_head_to_head

def analyze_fixture(fixture):
    try:
        home_team = fixture["teams"]["home"]["name"]
        away_team = fixture["teams"]["away"]["name"]
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        
        # H2H Data Fetch
        h2h_data = fetch_team_head_to_head(home_id, away_id)
        
        home_wins = 0
        away_wins = 0
        draws = 0
        total_goals = 0
        both_scored_matches = 0
        
        total_h2h = len(h2h_data)
        
        if total_h2h > 0:
            for match in h2h_data:
                goals_home = match["goals"]["home"] or 0
                goals_away = match["goals"]["away"] or 0
                match_goals = goals_home + goals_away
                total_goals += match_goals
                
                if goals_home > 0 and goals_away > 0:
                    both_scored_matches += 1
                    
                if match["teams"]["home"]["winner"]:
                    if match["teams"]["home"]["id"] == home_id:
                        home_wins += 1
                    else:
                        away_wins += 1
                elif match["teams"]["away"]["winner"]:
                    if match["teams"]["away"]["id"] == away_id:
                        away_wins += 1
                    else:
                        home_wins += 1
                else:
                    draws += 1
            
            avg_goals = total_goals / total_h2h
            btts_prob = (both_scored_matches / total_h2h) * 100
        else:
            # Default fallback when H2H is empty
            avg_goals = 2.0
            btts_prob = 50.0

        # Predictions Logic
        if home_wins >= away_wins and home_wins >= draws:
            match_winner = f"{home_team} (Home Win or Draw)"
            double_chance = "1X"
        elif away_wins >= home_wins and away_wins >= draws:
            match_winner = f"{away_team} (Away Win or Draw)"
            double_chance = "X2"
        else:
            match_winner = "Draw Probable"
            double_chance = "1X"

        over_under = "Over 2.5 Goals" if avg_goals > 2.5 else "Under 2.5 Goals"
        btts = "Yes" if btts_prob >= 50 else "No"

        result_text = (
            f"⚽ **{home_team} vs {away_team}**\n\n"
            f"📊 **পরিসংখ্যান বিশ্লেষণ (গত {total_h2h} টি ম্যাচ):**\n"
            f"• গড় গোল: {avg_goals:.1f}\n"
            f"• উভয় দলের গোলের হার: {btts_prob:.0f}%\n\n"
            f"🎯 **প্রেডিকশন (Prediction):**\n"
            f"1️⃣ **1X2 / Winner:** {match_winner}\n"
            f"2️⃣ **Double Chance:** {double_chance}\n"
            f"3️⃣ **Over/Under 2.5:** {over_under}\n"
            f"4️⃣ **Both Teams to Score (BTTS):** {btts}\n\n"
            f"⚠️ *টিপস: তথ্যভিত্তিক বিশ্লেষণ প্রদান করা হয়েছে, শতভাগ নিশ্চয়তা দেওয়া সম্ভব নয়।*"
        )
        return result_text

    except Exception as e:
        return f"এনালাইসিস করতে সমস্যা হয়েছে: {e}"
          
