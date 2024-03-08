import re
import json
from enum import Enum
from itertools import zip_longest

from src.service.llm_service import Conversation, LLMService, Role


class EvaluationCategory(Enum):
    CORRECT = "Correct"
    PARTIALLY_CORRECT = "Partially Correct"
    INCORRECT = "Incorrect"


class FunctionError(Enum):
    LIST_PASSING_ERROR = "Algorithms fail to transfer the entire list of elements outputted by the first function to the second function  sequential multiple function calling, resulting in incomplete data processing and potential inaccuracies."
    INCOMPLETE_FUNCTION_INVOCATION = "This occurs when the model fails to call the second function due to lack of reasoning or misunderstanding of the provided user request. The model might not recognize the need for calling the second or third function or may incorrectly assume that the task can be accomplished with just one function call."


class ParameterError(Enum):
    PARAMETER_HALLUCINATION = "Parameters generated by the model are completely unrelated to the task at hand or the information provided in the user query, indicating a disconnect between the generated output and the intended context."
    NUMERIC_VALUES_MISINTERPRETATION = "The model misinterprets numeric sequences in the input text, such as phone numbers and postal codes, as non-numeric entities, such as listing names or other textual elements."
    NON_LATIN_CHARACTER_MISINTERPRETATION = "The algorithms misinterpret characters from non-Latin scripts, leading to errors in comprehension and analysis of text inputs. The model encounters difficulties in accurately translating the user's request from one language to another, resulting in incorrect parameter values being passed to the function."
    INCORRECT_STRING_HANDLING = "This error occurs in three cases: Firstly, when the user gives some context which may confuse the algorithms and incorrectly extract string elements for parameter values based on the context provided by the user in the prompt. Secondly, when it fails to handle string synonyms, which results in the extracted parameter to deviate from the expected string representation format of the expected parameter value in the dataset used, and therefore briniging inaccuracies when the function is called. Lastly,  when dealing with user typos, the algorithms struggle to identify and correct them, resulting in incorrect parameter values being selected."
    CURRENCY_AND_CONVERSION_ERROR = "This error occurs in two situations: the algorithms are unable to perform accurate currency conversion from the currency format provided by the user to the currency type used in the dataset API and additionaly when the algorithms inaccurately convert currency values provided in different units by the user’s prompt (e.g., dollars and cents) to the currency unit of the same currency type used in the dataset API."
    TEXT_TO_NUMBER_CONVERSION_ERROR = "The algorithms struggle to recognize and convert textual representations of time, such as 'week', into their corresponding numeric values (e.g., 7 days). {Failure to Convert Textual-Time-Unit to Numeric Value}. Additionally, they encounter errors or failures when attempting to perform division or other mathematical operations with numerical values represented as text within user prompts."
    DATE_INTERENCE_ERROR = "This error category includes the questions where the GPT4.5 model returned inaccurate answers because of encountering difficulties  in inferring dates from user inputs or contextual cues.  (add travel use case description also)"


class ResponseError(Enum):
    ARITHMETIC_ERROR = "This error category includes the questions where inaccurate answers we returned due to computational errors in performing arithmetic computations including summation or averaging."
    NUMERICAL_SORTING_ERROR = "This error category includes the questions where inaccurate answers were returned due to probable anomalies in sorting numerical values, particularly when requested to identify specific positions of any attribute such as the third highest or lowest value"
    DATE_SORTING_ERROR = "This error category includes the questions where inaccurate answers were returned due to probable anomalies in sorting date values, particularly when queried to return result according to some criteria such as the third most recent or least recent date etc."
    COUNTING_DISCREPANCY = "This error category includes the questions where inaccurate answers were returned due to discrepancies or inaccuracies in counting elements or results. "
    RELIANCE_ON_PREVIOUS_KNOWLEDGE = "This error category includes the questions where incorrect answers were returned due to use of its previous knowledge base rather than relying on getting information from the results of called functions"
    MULTI_INFORMATION_RETRIEVAL_ERROR = "This error category includes the questions where challenges or inaccuracies were encountered in retrieving multiple pieces of information or results. Instead of retrieving all relevant information that satisfies the specified criteria, the model returned  responses with errors, such as providing incomplete results or selecting records that meet only  one of the multiple specified criteria."
    QUERY_MISINTERPRETATION = "This error category includes those questions where inaccurate answers were returned due to difficulty in interpreting user queries. It includes cases where the model failed to grasp the context of user queries, misinterprets specific keywords or phrases, or the intent behind the question by the user"
    INACCURACY = "This  error category includes questions where the responses lack  contextual relevance and specificity, leading to inaccuracies or inadequacies in addressing user queries. It also includes the case where the provided information only partially aligns with the user query, resulting in incomplete or additional irrelevant responses."


def error_category_to_dict(group: ParameterError | FunctionError | ResponseError) -> dict[str:str]:
    return {error.name: error.value for error in group}


class Evaluator:

    @staticmethod
    def eval_arguments(called_path: list[dict], correct_paths: list[dict]) -> tuple[int, EvaluationCategory]:

        def compare_params(called_params: dict, target_params: dict):
            matches = 0
            matching_status = EvaluationCategory.INCORRECT

            if called_params is None or target_params is None:
                pass  # "Incorrect", 0
            elif called_params == target_params:
                matches = len(called_params)
                matching_status = EvaluationCategory.CORRECT
            else:
                matching_keys = called_params.keys() & target_params.keys()
                if len(matching_keys) > 0:
                    matches = len(
                        [True for key in matching_keys if called_params[key] == target_params[key]])
                    if matches > 0:
                        matching_status = EvaluationCategory.PARTIALLY_CORRECT

            return matching_status, matches

        def compare_paths(called_path: list[dict], correct_path: list[dict]):
            matches = 0
            best_status = None
            for called_params, target_params in zip_longest(called_path, correct_path["parameters"]):

                s, m = compare_params(called_params, target_params)
                matches += m

                if best_status is None:
                    best_status = s

                elif best_status == EvaluationCategory.CORRECT and (s == EvaluationCategory.PARTIALLY_CORRECT or s == EvaluationCategory.INCORRECT):
                    best_status = EvaluationCategory.PARTIALLY_CORRECT

                elif best_status == EvaluationCategory.INCORRECT and (s == EvaluationCategory.PARTIALLY_CORRECT or EvaluationCategory.CORRECT):
                    best_status = EvaluationCategory.PARTIALLY_CORRECT

            return best_status, matches

        # Edge Case: target is no function call (and model calls none)
        if len(called_path) == 0 and len(correct_paths) == 1 and len(correct_paths[0]["parameters"]) == 0:
            return 0, EvaluationCategory.CORRECT

        best_matches = 0
        best_status = EvaluationCategory.INCORRECT
        for correct_path in correct_paths:

            matching_status, matches = compare_paths(called_path, correct_path)

            if matches > best_matches:
                best_matches = matches
                best_status = matching_status

            if matching_status == EvaluationCategory.CORRECT:
                break

        return best_matches, best_status

    @staticmethod
    def eval_functions(called_functions: list[str], correct_paths: list[dict]) -> tuple[int, EvaluationCategory]:
        best_status = EvaluationCategory.INCORRECT
        best_match_count = 0

        for path in correct_paths:
            correct_functions = path["functions"]
            match_count = sum(1 for a, b in zip(
                called_functions, correct_functions) if a == b)

            if match_count > best_match_count:
                best_match_count = match_count
                if match_count == len(called_functions) == len(correct_functions):
                    best_status = EvaluationCategory.CORRECT
                elif match_count > 0:
                    best_status = EvaluationCategory.PARTIALLY_CORRECT

        return best_match_count, best_status

    @staticmethod
    def eval_response(model_response: dict, target_responses: dict) -> EvaluationCategory:

        def eval_response(model_response: dict, target_response: dict, ordered_items: dict):

            if model_response.keys() != target_response.keys():
                return False

            for key in model_response.keys():
                if key in ordered_items:
                    if sorted(model_response[key]) != sorted(target_response[key]):
                        return False

                else:
                    if model_response[key] != target_response[key]:
                        return False

            return True

        model_response = re.sub("'", '"', model_response)
        model_response = re.sub('(?<=\w)"(?=\w)', "'", model_response)

        print(model_response)

        model_response_raw = re.search(
            r'\{(.|\n)*?\}', model_response, re.DOTALL)

        if model_response_raw:
            try:
                model_response = json.loads(model_response_raw.group())
                if eval_response(model_response, target_responses["answer"], target_responses["ordered_items"]):
                    return EvaluationCategory.CORRECT

            except json.JSONDecodeError:
                print(model_response)
                # raise Exception  # TODO #IllegalJSONResponseFormat("...")
                return EvaluationCategory.INCORRECT

        return EvaluationCategory.INCORRECT

    @staticmethod
    def eval_error_category(llm_service: LLMService, conversation: Conversation, target: dict, function_eval: EvaluationCategory, arguments_eval: EvaluationCategory, response_eval: EvaluationCategory) -> FunctionError | ParameterError | ResponseError | None:
        """
        Heuristics:
        if function invocation is not correct -> FunctionError
        elif parameter invocation is not correct -> ParameterError
        elif response evaluation is not correct -> ResponseError
        """
        eval_error_mapping = {
            function_eval: FunctionError,
            arguments_eval: ParameterError,
            response_eval: ResponseError
        }

        evaluations = {
            'Called functions': function_eval,
            'Called arguments': arguments_eval,
            'Returned response': response_eval
        }

        solution_paths = target["solution_paths"]
        solution_answer = target["expected_answer"]

        if function_eval.value == EvaluationCategory.CORRECT and arguments_eval.value == EvaluationCategory.CORRECT and response_eval.value == EvaluationCategory.CORRECT:
            return None

        prompt = f""" 
            For the previous conversation, and the following evaluations:
            {', '.join([f"{key}: {value.value}" for key, value in evaluations.items()])}

            For the possible solution paths and answer:
            Possible paths: {str(solution_paths)}
            Correct answer: {str(solution_answer)}

            What is the most fitting error category?
            {str(error_category_to_dict(FunctionError))}.
            {str(error_category_to_dict(ParameterError))}.
            {str(error_category_to_dict(ResponseError))}.

            Return the name of the category.
            """

        conversation.add({"role": Role.USER.value, "content": prompt})
        response, _ = llm_service.start_chat(conversation)

        return response
