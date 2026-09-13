
def task_a(user_choice):
    return f"dummy composed payload from user input: {user_choice}"

def task_b(task_a_output, index):
    words = task_a_output.split(":")[1].split()
    return f"my preferred word is {words[index-1]}"

def task_subb(task_b_output, index):
    return task_b_output.replace("my preferred word is ", "")

def task_c(task_b_all):
    accumulated = {
        "selected_words": set(t for r in task_b_all for t in r),
        "len": len(task_b_all)
    }

    return accumulated

def task_d(task_c_output):
    return task_c_output["len"] > 3

def task_e(preferred_letter, task_c_result, index):
    words = task_c_result["selected_words"]
    graded = {}
    for w in words:
        score = w.count(preferred_letter)
        graded[w] = score

    return graded

def task_f(all_task_e):
    final_set = set()
    for resp in all_task_e:
        for k, v in resp.items():
            if v >= 1:
                final_set.add(k)
    return sorted(list(final_set))

def flow1(user_choice, preferred_letter):
    a_result = task_a(user_choice)

    all_rounds = []
    complete = False
    loop_count = 0
    while not complete:
        if loop_count > 10:
            raise RuntimeError("could not complete after max 10 iterations")

        loop_count += 1
        round_results = []
        for i in range(1,4):
            b_result = task_b(a_result, i)
            subb_result = task_subb(b_result, i)
            round_results.append(subb_result)
        all_rounds.append(round_results)
        
        task_c_result = task_c(all_rounds)
        complete = task_d(task_c_result)

  
    all_e = []
    for i in range(1,4):
        e_result = task_e(preferred_letter, task_c_result, i)
        all_e.append(e_result)
    
    f_result = task_f(all_e)

    return f_result


#### outer flow ####

def task_a2(loop_count, flow1_result):
    # let's pretend we needed to run flow1 twice
    if loop_count >= 2:
        return True
    return False


def flow2(user_choice, preferred_letter):

    complete = False
    loop_count = 0
    loop_runs = []
    while not complete:
        loop_count += 1
        if loop_count >= 5:
            raise RuntimeError("max cycles reached")
        flow1_result = flow1(user_choice, preferred_letter)

        a2_result = task_a2(loop_count, flow1_result)

        if a2_result:
            break

if __name__ == "__main__":
    flow2_result = flow2("potato tomate berry watermelon", "t")
    print("flow2_result", flow2_result)