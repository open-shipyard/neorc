def compose_payload(sentence):
    return f"dummy composed payload from user input: {sentence}"


def pick_word(payload, index):
    words = payload.split(":")[1].split()
    return f"my preferred word is {words[index - 1]}"


def extract_word(picked, index):
    return picked.replace("my preferred word is ", "")


def collect_words(all_rounds):
    accumulated = {
        "selected_words": set(t for r in all_rounds for t in r),
        "len": len(all_rounds),
    }

    return accumulated


def enough_rounds(collection):
    return collection["len"] > 3


def score_words(preferred_letter, collection, index):
    words = collection["selected_words"]
    graded = {}
    for w in words:
        score = w.count(preferred_letter)
        graded[w] = score

    return graded


def keep_matching_words(all_scores):
    final_set = set()
    for resp in all_scores:
        for k, v in resp.items():
            if v >= 1:
                final_set.add(k)
    return sorted(list(final_set))


def word_picker(sentence, preferred_letter):
    payload = compose_payload(sentence)

    all_rounds = []
    complete = False
    loop_count = 0
    while not complete:
        if loop_count > 10:
            raise RuntimeError("could not complete after max 10 iterations")

        loop_count += 1
        round_words = []
        for i in range(1, 4):
            picked = pick_word(payload, i)
            word = extract_word(picked, i)
            round_words.append(word)
        all_rounds.append(round_words)

        collection = collect_words(all_rounds)
        complete = enough_rounds(collection)

    all_scores = []
    for i in range(1, 4):
        scores = score_words(preferred_letter, collection, i)
        all_scores.append(scores)

    matching_words = keep_matching_words(all_scores)

    return matching_words


#### outer flow ####


def ran_enough(loop_count, picked_words):
    # let's pretend we needed to run word_picker twice
    return loop_count >= 2


def word_picker_rounds(sentence, preferred_letter):

    complete = False
    loop_count = 0
    while not complete:
        loop_count += 1
        if loop_count >= 5:
            raise RuntimeError("max cycles reached")
        picked_words = word_picker(sentence, preferred_letter)

        done = ran_enough(loop_count, picked_words)

        if done:
            break


if __name__ == "__main__":
    result = word_picker_rounds("potato tomate berry watermelon", "t")
    print("result", result)
